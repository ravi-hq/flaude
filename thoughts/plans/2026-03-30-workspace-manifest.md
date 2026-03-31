# Workspace Manifest Implementation Plan

## Overview

After repositories are cloned in the container, emit a structured JSON manifest listing all workspace files. This gives downstream consumers visibility into what files are available before Claude Code runs. Follows the existing `[flaude:exit:N]` tagged-log-line pattern — zero new infrastructure.

## Research Summary

Research conducted by agent team with 3 specialist tracks:
- **Entrypoint/Cloning**: Identified insertion point at `entrypoint.sh:144` (after `cd "$WORKSPACE"` + CWD persistence, before Claude runs). `find` and `jq` available in container. Must exclude `.git` and `node_modules`.
- **Log Drain Pipeline**: Confirmed `[flaude:exit:N]` is the canonical tag pattern. Messages flow as plain strings — `parse_log_entry` passes content verbatim. No changes needed to log drain infrastructure.
- **Consumer/Runner Side**: `RunResult` is a frozen dataclass with 4 fields. `StreamingRun.result()` already scans collected logs for exit markers at `lifecycle.py:147-149`. Three `RunResult` construction sites: `runner.py:325`, `runner.py:426`, `session.py:137`.

### Key Discoveries:
- `[flaude:exit:N]` pattern at `runner.py:34-60` is the exact precedent — regex on collected logs, scanned in reverse
- `StreamingRun._collected_logs` accumulates all lines during iteration (`lifecycle.py:101`)
- `run_and_destroy` and `run_session_turn` do NOT collect logs — `workspace_files` will be empty on those paths (acceptable for MVP)
- Docker container has `find` (GNU coreutils) and `jq` (explicitly installed) — sufficient for manifest generation
- `--depth 1` shallow clones still include `.git` directory — must exclude

## Current State Analysis

- `entrypoint.sh` emits structured markers (`[flaude:exit:N]`, `[flaude:session:<id>]`) that flow through the log drain as plain strings
- `extract_exit_code_from_logs()` demonstrates the extraction pattern: regex match on collected log lines, returns typed value
- `RunResult` is a simple frozen dataclass — adding an optional field is backwards-compatible
- No existing file listing or manifest functionality exists

## Desired End State

1. After cloning, `entrypoint.sh` emits a `[flaude:manifest:{"workspace":"...","files":["...",...]}]` line to stdout
2. `RunResult` has a `workspace_files: tuple[str, ...]` field (empty tuple default)
3. `StreamingRun.result()` extracts the manifest from collected logs and populates `workspace_files`
4. `extract_workspace_manifest_from_logs()` is a public utility (like `extract_exit_code_from_logs`)
5. Downstream callers access `result.workspace_files` for the file list

**Verification**: `result.workspace_files` is a non-empty tuple of relative file paths after a run that clones repos via `run_with_logs`.

## What We're NOT Doing

- No changes to `LogDrainServer`, `LogCollector`, or `parse_log_entry` — the tag flows through as-is
- No manifest retrieval for `run_and_destroy` or `run_session_turn` (no log collection on those paths)
- No volume-based manifest persistence for sessions
- No `tree` command installation — `find` + `jq` is sufficient
- No nested tree structure — flat list of relative paths
- No file metadata (size, mtime) — just paths

## Implementation Approach

Follow the `[flaude:exit:N]` pattern exactly:
1. Bash emits a tagged line → 2. Python regex extracts the payload → 3. Typed field on `RunResult`

The manifest is emitted once after cloning completes (skipped on session resume since workspace is already populated). The JSON payload is compact (single line) to avoid issues with multi-line log entries.

## File Ownership Map

Designed for parallel execution via `team-implement`:

| File | Phase | Owner Track | Change Type |
|------|-------|-------------|-------------|
| `flaude/entrypoint.sh` | 1 | entrypoint | modify |
| `flaude/runner.py` | 2 | python | modify |
| `flaude/lifecycle.py` | 2 | python | modify |
| `flaude/__init__.py` | 2 | python | modify |
| `tests/test_workspace_manifest.py` | 3 | tests | create |

**Conflict-free guarantee**: No file appears in multiple owner tracks within the same phase. Phase 3 depends on phases 1 and 2.

---

## Phase 1: Emit Manifest from Entrypoint

### Overview
Add workspace file manifest emission to `entrypoint.sh` after cloning completes and CWD is set, before Claude Code runs.

### Changes Required

#### 1. Add `emit_manifest` function and call it
**File**: `flaude/entrypoint.sh`
**Insert after**: Line 143 (after CWD persistence block, before output format args)

```bash
# --- Emit workspace file manifest ---
emit_manifest() {
    local manifest_json
    manifest_json=$(find . \
        -not -path '*/.git/*' -not -path '*/.git' \
        -not -path '*/node_modules/*' -not -path '*/node_modules' \
        -not -path './.DS_Store' \
        -type f \
        | sort \
        | jq -R . | jq -sc "{workspace: \"$PWD\", files: .}")
    echo "[flaude:manifest:${manifest_json}]"
}

emit_manifest
```

**Key details**:
- `find . -type f` lists files only (no directories) relative to `$PWD`
- Excludes `.git/`, `node_modules/`, `.DS_Store`
- `jq -R .` reads each line as a JSON string, `jq -sc` collects into a compact single-line array
- Runs after `cd "$WORKSPACE"` so paths are relative to workspace root
- On session resume, the clone block is skipped but manifest is still emitted (workspace exists, files are present) — this is correct since downstream may need the manifest on every turn

### Success Criteria

#### Automated Verification:
- [ ] `shellcheck flaude/entrypoint.sh` passes
- [ ] Running the entrypoint in a test container emits a `[flaude:manifest:{...}]` line

#### Manual Verification:
- [ ] The manifest line contains expected files from a cloned repo
- [ ] `.git` contents are excluded
- [ ] JSON is valid and compact (single line)

---

## Phase 2: Extract Manifest into RunResult

### Overview
Add `workspace_files` field to `RunResult`, create extraction function, wire into `StreamingRun.result()`.

### Changes Required

#### 1. Add extraction function
**File**: `flaude/runner.py`
**Insert after**: `extract_exit_code_from_logs` function (line 60)

```python
# Regex for the workspace manifest marker: [flaude:manifest:{...}]
_MANIFEST_MARKER_RE = re.compile(r"\[flaude:manifest:(\{.*\})\]")


def extract_workspace_manifest_from_logs(logs: list[str]) -> tuple[str, ...]:
    """Parse the ``[flaude:manifest:{...}]`` marker written by *entrypoint.sh*.

    Scans *logs* for the manifest marker and returns the file list.
    Returns an empty tuple if no marker is found.

    Args:
        logs: Log lines collected from the machine's stdout/stderr.

    Returns:
        A tuple of relative file paths found in the workspace.
    """
    for line in logs:
        m = _MANIFEST_MARKER_RE.search(line)
        if m:
            try:
                data = json.loads(m.group(1))
                return tuple(data.get("files", []))
            except (json.JSONDecodeError, TypeError):
                return ()
    return ()
```

Note: scans forward (not reversed like exit code) since manifest is emitted early in the run.

#### 2. Add `workspace_files` field to `RunResult`
**File**: `flaude/runner.py`
**Modify**: `RunResult` dataclass (line 116)

```python
@dataclass(frozen=True)
class RunResult:
    """Result of a completed flaude execution."""

    machine_id: str
    exit_code: int | None
    state: str
    destroyed: bool
    workspace_files: tuple[str, ...] = ()
```

The default `()` makes this backwards-compatible — all existing construction sites continue working without changes. They'll get an empty tuple.

#### 3. Wire extraction into `StreamingRun.result()`
**File**: `flaude/lifecycle.py`
**Modify**: `result()` method (after line 149, before the failure check)

Add import of `extract_workspace_manifest_from_logs` alongside existing `extract_exit_code_from_logs` import.

```python
from flaude.runner import (
    RunResult,
    extract_exit_code_from_logs,
    extract_workspace_manifest_from_logs,
    run_and_destroy,
)
```

In `result()`, after exit code extraction and before the failure check:

```python
        effective_exit_code = run_result.exit_code
        if effective_exit_code is None:
            effective_exit_code = extract_exit_code_from_logs(self._collected_logs)

        # Extract workspace manifest from logs
        workspace_files = extract_workspace_manifest_from_logs(self._collected_logs)

        if raise_on_failure and _is_failure(effective_exit_code, run_result.state):
            raise MachineExitError(
                machine_id=run_result.machine_id,
                exit_code=effective_exit_code,
                state=run_result.state,
                logs=self._collected_logs,
            )

        # Return enriched result with workspace files if found
        if workspace_files:
            return RunResult(
                machine_id=run_result.machine_id,
                exit_code=effective_exit_code,
                state=run_result.state,
                destroyed=run_result.destroyed,
                workspace_files=workspace_files,
            )

        return run_result
```

Note: we construct a new `RunResult` only when manifest data is found, to preserve the original exit code override behavior and avoid mutating the frozen dataclass.

#### 4. Export from `__init__.py`
**File**: `flaude/__init__.py`
**Modify**: imports and `__all__`

Add `extract_workspace_manifest_from_logs` to the import from `flaude.runner` and to `__all__`.

### Success Criteria

#### Automated Verification:
- [ ] `cd /Users/jake/dev/ravi-hq/flying-claude && uv run python -c "from flaude import RunResult; r = RunResult('m1', 0, 'stopped', True); assert r.workspace_files == ()"`
- [ ] `uv run python -c "from flaude import extract_workspace_manifest_from_logs; assert extract_workspace_manifest_from_logs([]) == ()"`
- [ ] Type checking passes (if configured)
- [ ] Existing tests pass (backwards-compatible default)

#### Manual Verification:
- [ ] `RunResult` repr shows `workspace_files` field
- [ ] Existing code that constructs `RunResult` without `workspace_files` still works

**Gate**: Pause for human verification before proceeding to Phase 3.

---

## Phase 3: Tests

### Dependencies
- Requires Phase 1 (entrypoint changes) and Phase 2 (Python extraction) complete

### Changes Required

#### 1. Unit tests for extraction function
**File**: `tests/test_workspace_manifest.py` (new)

```python
"""Tests for workspace manifest extraction from log lines."""

import json

from flaude.runner import extract_workspace_manifest_from_logs


class TestExtractWorkspaceManifest:
    """extract_workspace_manifest_from_logs — unit tests."""

    def test_extracts_files_from_manifest_marker(self):
        manifest = json.dumps({"workspace": "/workspace/myrepo", "files": ["./src/main.py", "./README.md"]})
        logs = [
            "[flaude] Starting execution",
            "[flaude] Cloning repos...",
            f"[flaude:manifest:{manifest}]",
            "Claude output here",
        ]
        assert extract_workspace_manifest_from_logs(logs) == ("./src/main.py", "./README.md")

    def test_returns_empty_tuple_when_no_marker(self):
        logs = ["[flaude] Starting execution", "some output"]
        assert extract_workspace_manifest_from_logs(logs) == ()

    def test_returns_empty_tuple_for_empty_logs(self):
        assert extract_workspace_manifest_from_logs([]) == ()

    def test_returns_empty_tuple_for_malformed_json(self):
        logs = ["[flaude:manifest:not-json]"]
        assert extract_workspace_manifest_from_logs(logs) == ()

    def test_returns_empty_tuple_for_missing_files_key(self):
        logs = ['[flaude:manifest:{"workspace":"/workspace"}]']
        assert extract_workspace_manifest_from_logs(logs) == ()

    def test_handles_large_file_list(self):
        files = [f"./src/file_{i}.py" for i in range(500)]
        manifest = json.dumps({"workspace": "/workspace", "files": files})
        logs = [f"[flaude:manifest:{manifest}]"]
        result = extract_workspace_manifest_from_logs(logs)
        assert len(result) == 500
        assert result[0] == "./src/file_0.py"

    def test_first_marker_wins(self):
        m1 = json.dumps({"workspace": "/w", "files": ["./a.py"]})
        m2 = json.dumps({"workspace": "/w", "files": ["./b.py"]})
        logs = [f"[flaude:manifest:{m1}]", f"[flaude:manifest:{m2}]"]
        assert extract_workspace_manifest_from_logs(logs) == ("./a.py",)

    def test_coexists_with_exit_marker(self):
        """Manifest and exit markers in same log stream."""
        manifest = json.dumps({"workspace": "/workspace", "files": ["./main.py"]})
        logs = [
            f"[flaude:manifest:{manifest}]",
            "Claude output",
            "[flaude:exit:0]",
        ]
        assert extract_workspace_manifest_from_logs(logs) == ("./main.py",)


class TestRunResultWorkspaceFiles:
    """RunResult.workspace_files field."""

    def test_default_is_empty_tuple(self):
        from flaude.runner import RunResult
        r = RunResult(machine_id="m1", exit_code=0, state="stopped", destroyed=True)
        assert r.workspace_files == ()

    def test_accepts_workspace_files(self):
        from flaude.runner import RunResult
        r = RunResult(
            machine_id="m1", exit_code=0, state="stopped",
            destroyed=True, workspace_files=("./a.py", "./b.py"),
        )
        assert r.workspace_files == ("./a.py", "./b.py")
```

### Success Criteria

#### Automated Verification:
- [ ] `cd /Users/jake/dev/ravi-hq/flying-claude && uv run pytest tests/test_workspace_manifest.py -v` passes
- [ ] `uv run pytest` (full suite) passes — no regressions

---

## Testing Strategy

### Automated:
- Unit tests for `extract_workspace_manifest_from_logs()` covering: happy path, no marker, empty logs, malformed JSON, missing keys, large file lists, multiple markers, coexistence with exit marker
- Unit tests for `RunResult` field default and explicit values
- Existing test suite regression check

### Manual Testing Steps:
1. Run a real `run_with_logs` execution with a repo clone and verify `result.workspace_files` is populated
2. Verify the manifest line appears in `StreamingRun` log iteration
3. Verify `run_and_destroy` still works (returns empty `workspace_files`)
4. Check manifest excludes `.git` and `node_modules` entries

## Performance Considerations

- `find` on a `--depth 1` shallow clone is fast (no deep history). For typical repos (hundreds to low thousands of files) this adds <1s.
- The manifest JSON is a single line. For a repo with 1000 files averaging 30 chars per path, that's ~35KB — well within log drain limits.
- `jq -sc` produces compact JSON (no whitespace) to minimize log line size.
- Regex scan of collected logs is O(n) on number of log lines — negligible since manifest is emitted early and we return on first match.

## References

- Exit code extraction pattern: `flaude/runner.py:34-60`
- Log drain pipeline: `flaude/log_drain.py:121-189` (parse_log_entry)
- StreamingRun result extraction: `flaude/lifecycle.py:147-159`
- Entrypoint clone flow: `flaude/entrypoint.sh:28-125`
- Prior plan using same pattern: `thoughts/plans/2026-03-30-serverless-sessions.md`
