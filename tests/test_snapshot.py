"""Tests for flaude.snapshot — SnapshotRef, SnapshotBackend protocol, S3SnapshotBackend.

S3 tests use moto to provide a local mock of the S3 API.  boto3 and moto
are dev dependencies; the tests are skipped automatically if they are not
installed (they always are in the dev environment).

The ``s3_backend`` fixture activates the moto mock_aws context for the
duration of each test.  All boto3 calls (including those run via
run_in_executor) use the mocked backend — no real S3 calls are made.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tarfile
import tempfile
from collections.abc import Generator

import pytest

# ---------------------------------------------------------------------------
# Conditional import of boto3 / moto — skip tests if unavailable
# ---------------------------------------------------------------------------

boto3 = pytest.importorskip("boto3", reason="boto3 not installed (pip install flaude[s3])")
moto = pytest.importorskip("moto", reason="moto not installed (pip install moto[s3])")

from moto import mock_aws  # noqa: E402 — after importorskip

from flaude.snapshot import S3SnapshotBackend, SnapshotRef  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BUCKET = "test-flaude-snapshots"
KEY_PREFIX = "snaps"
REGION = "us-east-1"


@pytest.fixture()
def s3_backend() -> Generator[S3SnapshotBackend, None, None]:
    """S3SnapshotBackend backed by a moto-mocked S3.

    Activates mock_aws for the entire test so that both the backend's
    run_in_executor calls and any inline boto3 clients use the mock.
    """
    with mock_aws():
        import boto3 as b3

        b3.client("s3", region_name=REGION).create_bucket(Bucket=BUCKET)
        yield S3SnapshotBackend(
            bucket=BUCKET,
            key_prefix=KEY_PREFIX,
            aws_access_key_id="test",
            aws_secret_access_key="test",
            region_name=REGION,
        )


def _make_tar(tmp_dir: str, content: bytes = b"hello flaude") -> str:
    """Create a small tar.gz in *tmp_dir* with one file; return its path."""
    data_path = os.path.join(tmp_dir, "data.txt")
    with open(data_path, "wb") as fh:
        fh.write(content)

    tar_path = os.path.join(tmp_dir, "volume.tar.gz")
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(data_path, arcname="data.txt")

    return tar_path


# ---------------------------------------------------------------------------
# SnapshotRef
# ---------------------------------------------------------------------------


def test_snapshot_ref_is_frozen() -> None:
    """SnapshotRef is immutable (frozen dataclass)."""
    ref = SnapshotRef(
        backend="s3",
        uri="s3://bucket/key.tar.gz",
        size_bytes=100,
        checksum="abc",
        created_at="2026-01-01T00:00:00+00:00",
    )
    with pytest.raises((AttributeError, TypeError)):
        ref.backend = "r2"  # type: ignore[misc]


def test_snapshot_ref_default_version() -> None:
    """SnapshotRef.version defaults to 1."""
    ref = SnapshotRef(
        backend="s3",
        uri="s3://b/k",
        size_bytes=0,
        checksum="",
        created_at="",
    )
    assert ref.version == 1


def test_snapshot_ref_custom_version() -> None:
    """SnapshotRef accepts explicit version values."""
    ref = SnapshotRef(
        backend="r2",
        uri="s3://b/k",
        size_bytes=0,
        checksum="",
        created_at="",
        version=2,
    )
    assert ref.version == 2


# ---------------------------------------------------------------------------
# S3SnapshotBackend — upload
# The s3_backend fixture manages the mock_aws context; no @mock_aws needed.
# ---------------------------------------------------------------------------


async def test_s3_upload_returns_ref(s3_backend: S3SnapshotBackend) -> None:
    """upload() returns a SnapshotRef with correct metadata."""
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = _make_tar(tmp, content=b"test data")
        size = os.path.getsize(tar_path)

        ref = await s3_backend.upload(local_tar_path=tar_path)

    assert ref.backend == "s3"
    assert ref.uri.startswith(f"s3://{BUCKET}/{KEY_PREFIX}/")
    assert ref.uri.endswith(".tar.gz")
    assert ref.size_bytes == size
    assert len(ref.checksum) == 64  # SHA-256 hex
    assert ref.version == 1
    assert "T" in ref.created_at  # ISO 8601


async def test_s3_upload_checksum_correct(s3_backend: S3SnapshotBackend) -> None:
    """upload() computes the correct SHA-256 checksum of the tarball."""
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = _make_tar(tmp, content=b"checksum test content")
        # sha256 is of the tar file itself
        expected_checksum = hashlib.sha256(open(tar_path, "rb").read()).hexdigest()
        ref = await s3_backend.upload(local_tar_path=tar_path)

    assert ref.checksum == expected_checksum


async def test_s3_upload_object_exists_in_bucket(s3_backend: S3SnapshotBackend) -> None:
    """After upload(), the object exists in the mocked S3 bucket."""
    import boto3 as b3

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = _make_tar(tmp)
        ref = await s3_backend.upload(local_tar_path=tar_path)

    key = ref.uri.split("/", 3)[-1]
    s3 = b3.client("s3", region_name=REGION)
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    assert obj["ContentLength"] > 0


async def test_s3_multiple_uploads_have_unique_uris(s3_backend: S3SnapshotBackend) -> None:
    """Each upload() produces a unique URI."""
    with tempfile.TemporaryDirectory() as tmp:
        tar1 = _make_tar(tmp, content=b"upload one")
        # Need a second tar in the same tmp dir with a different name
        tar2_path = os.path.join(tmp, "vol2.tar.gz")
        with tarfile.open(tar2_path, "w:gz"):
            pass
        ref1 = await s3_backend.upload(local_tar_path=tar1)
        ref2 = await s3_backend.upload(local_tar_path=tar2_path)

    assert ref1.uri != ref2.uri


# ---------------------------------------------------------------------------
# S3SnapshotBackend — download
# ---------------------------------------------------------------------------


async def test_s3_round_trip(s3_backend: S3SnapshotBackend) -> None:
    """upload() then download() recovers the original tarball byte-for-byte."""
    original_content = b"round-trip test data"

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = _make_tar(tmp, content=original_content)
        original_bytes = open(tar_path, "rb").read()

        ref = await s3_backend.upload(local_tar_path=tar_path)

        download_path = os.path.join(tmp, "downloaded.tar.gz")
        await s3_backend.download(ref, local_tar_path=download_path)

        downloaded_bytes = open(download_path, "rb").read()

    assert downloaded_bytes == original_bytes


async def test_s3_download_produces_valid_tar(s3_backend: S3SnapshotBackend) -> None:
    """The downloaded file is a valid tar archive containing the original files."""
    content = b"archived content"

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = _make_tar(tmp, content=content)
        ref = await s3_backend.upload(local_tar_path=tar_path)

        download_path = os.path.join(tmp, "restored.tar.gz")
        await s3_backend.download(ref, local_tar_path=download_path)

        with tarfile.open(download_path, "r:gz") as tf:
            members = tf.getnames()
            assert "data.txt" in members
            extracted = tf.extractfile("data.txt")
            assert extracted is not None
            assert extracted.read() == content


# ---------------------------------------------------------------------------
# S3SnapshotBackend — delete
# ---------------------------------------------------------------------------


async def test_s3_delete_removes_object(s3_backend: S3SnapshotBackend) -> None:
    """delete() removes the object from S3."""
    import boto3 as b3
    from botocore.exceptions import ClientError

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = _make_tar(tmp)
        ref = await s3_backend.upload(local_tar_path=tar_path)

    key = ref.uri.split("/", 3)[-1]
    await s3_backend.delete(ref)

    s3 = b3.client("s3", region_name=REGION)
    with pytest.raises(ClientError, match="NoSuchKey"):
        s3.get_object(Bucket=BUCKET, Key=key)


# ---------------------------------------------------------------------------
# S3SnapshotBackend — missing boto3
# ---------------------------------------------------------------------------


def test_s3_backend_missing_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """S3SnapshotBackend raises ImportError when boto3 is not installed."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "boto3":
            raise ImportError("No module named 'boto3'")
        return real_import(name, *args, **kwargs)

    # Remove boto3 from sys.modules cache so the import is re-executed
    monkeypatch.delitem(sys.modules, "boto3", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match="boto3 is required"):
        S3SnapshotBackend(bucket="b", key_prefix="p")
