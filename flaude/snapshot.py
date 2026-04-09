"""Pluggable snapshot backend protocol and S3-compatible implementation.

Snapshot backends handle uploading and downloading volume tarballs to/from
object storage (R2, S3, B2, etc.). The :class:`SnapshotBackend` protocol
defines the interface; :class:`S3SnapshotBackend` provides a boto3-backed
implementation for S3-compatible services.

Install the ``s3`` optional extra to use :class:`S3SnapshotBackend`::

    pip install flaude[s3]
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class SnapshotRef:
    """Immutable reference to an uploaded volume snapshot.

    Attributes:
        backend: Storage backend identifier (``"s3"``, ``"r2"``).
        uri: Object URI, e.g. ``"s3://bucket/prefix/snap.tar.gz"``.
        size_bytes: Compressed size of the uploaded tarball.
        checksum: SHA-256 hex digest of the tarball.
        created_at: ISO 8601 timestamp of when the snapshot was created.
        version: Snapshot format version (reserved for future migrations).
    """

    backend: str
    uri: str
    size_bytes: int
    checksum: str
    created_at: str
    version: int = 1


class SnapshotBackend(Protocol):
    """Protocol that every snapshot backend must implement.

    Implementations handle the actual I/O to object storage.  The
    ``local_tar_path`` arguments point to a local ``.tar.gz`` file that
    the caller is responsible for creating (on upload) or consuming (on
    download).
    """

    async def upload(self, *, local_tar_path: str) -> SnapshotRef:
        """Upload a local tarball and return a :class:`SnapshotRef`."""
        ...

    async def download(self, ref: SnapshotRef, *, local_tar_path: str) -> None:
        """Download the tarball described by *ref* to *local_tar_path*."""
        ...

    async def delete(self, ref: SnapshotRef) -> None:
        """Permanently delete the snapshot described by *ref*."""
        ...


def _sha256_file(path: str) -> str:
    """Return the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class S3SnapshotBackend:
    """S3-compatible snapshot backend (R2 / S3 / B2).

    Uses ``boto3`` for all object storage operations.  Install the ``s3``
    optional extra to pull in the dependency::

        pip install flaude[s3]

    Large volumes are uploaded via multipart upload to avoid buffering the
    entire tarball in memory.

    Args:
        bucket: S3 bucket name.
        key_prefix: Prefix prepended to every object key (no trailing slash).
        endpoint_url: Custom endpoint for S3-compatible services such as
            Cloudflare R2 or Backblaze B2.  Leave ``None`` for AWS S3.
        aws_access_key_id: AWS / service access key ID.
        aws_secret_access_key: AWS / service secret access key.
        region_name: AWS region (use ``"auto"`` for Cloudflare R2).
    """

    def __init__(
        self,
        *,
        bucket: str,
        key_prefix: str,
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        region_name: str = "auto",
    ) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise ImportError(
                "boto3 is required for S3SnapshotBackend. "
                "Install it with: pip install flaude[s3]"
            ) from exc

        self._bucket = bucket
        self._key_prefix = key_prefix.rstrip("/")
        self._endpoint_url = endpoint_url
        self._region_name = region_name

        session = boto3.Session(
            aws_access_key_id=aws_access_key_id or os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=aws_secret_access_key
            or os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region_name=region_name,
        )
        self._s3 = session.client(
            "s3",
            endpoint_url=endpoint_url,
        )

    def _make_key(self, snapshot_id: str) -> str:
        return f"{self._key_prefix}/{snapshot_id}.tar.gz"

    async def upload(self, *, local_tar_path: str) -> SnapshotRef:
        """Upload *local_tar_path* to S3 and return a :class:`SnapshotRef`.

        Uses multipart upload automatically for files larger than 8 MB
        (boto3 default threshold).

        Args:
            local_tar_path: Path to the local ``.tar.gz`` file.

        Returns:
            A :class:`SnapshotRef` pointing to the uploaded object.
        """
        import asyncio

        snapshot_id = str(uuid.uuid4())
        key = self._make_key(snapshot_id)

        checksum = _sha256_file(local_tar_path)
        size_bytes = os.path.getsize(local_tar_path)

        # Run blocking boto3 call in thread pool
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._s3.upload_file(local_tar_path, self._bucket, key),
        )

        uri = f"s3://{self._bucket}/{key}"
        return SnapshotRef(
            backend="s3",
            uri=uri,
            size_bytes=size_bytes,
            checksum=checksum,
            created_at=datetime.now(UTC).isoformat(),
        )

    async def download(self, ref: SnapshotRef, *, local_tar_path: str) -> None:
        """Download the snapshot described by *ref* to *local_tar_path*.

        Args:
            ref: The :class:`SnapshotRef` identifying the object.
            local_tar_path: Destination path for the downloaded tarball.
        """
        import asyncio

        # Extract key from URI: s3://bucket/key
        key = ref.uri.split("/", 3)[-1]

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._s3.download_file(self._bucket, key, local_tar_path),
        )

    async def delete(self, ref: SnapshotRef) -> None:
        """Delete the snapshot described by *ref* from S3.

        Args:
            ref: The :class:`SnapshotRef` identifying the object to delete.
        """
        import asyncio

        key = ref.uri.split("/", 3)[-1]

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._s3.delete_object(Bucket=self._bucket, Key=key),
        )
