# Stream JSON Output Format — Implementation Plan

## Overview

Add support for Claude Code's `--output-format stream-json` so that consumers of flaude's log stream receive structured NDJSON events (tool calls, assistant messages, cost/usage) instead of plain text. This enables downstream systems to identify logs as Claude Code output and process them programmatically.

## Research Summary

Research conducted by agent team with 3 specialist tracks:
- **stream-json-format**: Captured the full NDJSON schema from `claude -p --output-format stream-json --verbose`. Documented all message types: `system`, `assistant`, `stream_event`, `rate_limit_event`, `result`.
- **log-pipeline**: Confirmed the entire log pipeline (`LogDrainServer` -> `LogCollector` -> `LogStream` -> `StreamingRun`) requires zero code changes — it's content-agnostic, yielding `str` lines regardless of content.
- **config-entrypoint**: Mapped the single invocation site (`entrypoint.sh:123`) and the `MachineConfig` pattern for adding new env vars.

### Key Discoveries:
- `--output-format stream-json` **requires `--verbose`** — Claude Code exits with an error without it (`entrypoint.sh:123`)
- The `[flaude:exit:N]` marker is echoed by bash after `claude` exits, so it remains a plain-text line even when Claude outputs JSON — `extract_exit_code_from_logs` continues to work unchanged (`runner.py:31`)
- The log pipeline passes strings through regardless of content format — no type changes or parsing needed (`log_drain.py`, `lifecycle.py`)
- `ConcurrentExecutor` doesn't use the log drain at all — completely unaffected (`executor.py`)

## Current State Analysis

`entrypoint.sh` runs `claude -p -- "$FLAUDE_PROMPT"` which outputs plain text to stdout. The Fly log drain captures each stdout line as a `message` string in its NDJSON envelope. `LogStream` yields those strings to callers. There is no way for a log consumer to distinguish Claude Code output from arbitrary text, and structured information (tool calls, thinking, cost) is lost.

## Desired End State

When `MachineConfig(output_format="stream-json")` is set:
1. `entrypoint.sh` runs `claude -p --verbose --output-format stream-json -- "$FLAUDE_PROMPT"`
2. Each line in the log stream is a self-contained JSON object with a `type` field (`system`, `assistant`, `result`, etc.)
3. The `result` line contains `total_cost_usd`, `usage`, `duration_ms`, and the full text result
4. Callers that don't set `output_format` get the existing plain-text behavior (backward compatible)

Verification: run `claude -p --verbose --output-format stream-json -- "say hello"` and confirm each output line parses as JSON with a `type` field.

## What We're NOT Doing

- **Not parsing JSON in the log pipeline** — `LogStream` continues to yield raw `str`. Parsing is a caller concern.
- **Not adding `--include-partial-messages`** — can be added later as a separate opt-in flag if token-by-token streaming is needed.
- **Not changing the `LogEntry` or `LogStream` types** — they remain `str`-based.
- **Not making stream-json the default** — callers must opt in to avoid breaking existing consumers.

## Implementation Approach

The change is minimal and fully backward-compatible. We add one field to `MachineConfig`, one env var to `build_machine_config`, and conditional flag handling in `entrypoint.sh`. The log pipeline requires no changes because it's content-agnostic.

## File Ownership Map

| File | Phase | Owner Track | Change Type |
|------|-------|-------------|-------------|
| `flaude/machine_config.py` | 1 | backend | modify |
| `flaude/entrypoint.sh` | 1 | backend | modify |
| `tests/test_startup_env.py` | 2 | backend | modify |
| `tests/test_log_payload_parsing.py` | 2 | backend | modify |
| `docs/guide/streaming.md` | 3 | docs | modify |
| `flaude/Dockerfile` | 3 | docs | modify |

## Phase 1: Config & Entrypoint

### Overview
Wire `output_format` through from Python config to the shell entrypoint.

### Changes Required:

#### 1. MachineConfig — add `output_format` field
**File**: `flaude/machine_config.py`
**Changes**: Add field and env var injection.

```python
# In MachineConfig dataclass, after the `metadata` field:
output_format: str = ""  # e.g. "stream-json", "json", or "" for default (text)
```

```python
# In build_machine_config, after the metadata.update(config.metadata) block:
if config.output_format:
    env_vars["FLAUDE_OUTPUT_FORMAT"] = config.output_format
```

#### 2. entrypoint.sh — read env var and pass flags
**File**: `flaude/entrypoint.sh`
**Changes**: Build output format args conditionally. When `stream-json` is requested, also pass `--verbose` (required by Claude Code).

Replace the claude invocation block (lines 122-124):

```bash
# Build optional output format arguments
output_fmt_args=()
if [ -n "${FLAUDE_OUTPUT_FORMAT:-}" ]; then
    output_fmt_args+=(--output-format "$FLAUDE_OUTPUT_FORMAT")
    # stream-json requires --verbose
    if [ "$FLAUDE_OUTPUT_FORMAT" = "stream-json" ]; then
        output_fmt_args+=(--verbose)
    fi
fi

claude -p "${output_fmt_args[@]}" -- "$FLAUDE_PROMPT"
```

### Success Criteria:

#### Automated Verification:
- [ ] `cd /Users/jake/dev/ravi-hq/flying-claude && python -m pytest tests/test_startup_env.py -x`
- [ ] `cd /Users/jake/dev/ravi-hq/flying-claude && python -m pytest tests/ -x`

#### Manual Verification:
- [ ] `MachineConfig(output_format="stream-json", ...)` produces a payload with `FLAUDE_OUTPUT_FORMAT=stream-json` in env vars
- [ ] `MachineConfig(prompt="x", claude_code_oauth_token="y")` (no output_format) produces a payload without `FLAUDE_OUTPUT_FORMAT`

**Gate**: Tests pass before proceeding to Phase 2.

---

## Phase 2: Tests

### Dependencies
- Requires Phase 1 complete

### Changes Required:

#### 1. Entrypoint flag tests
**File**: `tests/test_startup_env.py`
**Changes**: Add tests to the `TestNonInteractiveMode` class.

```python
# Test: when FLAUDE_OUTPUT_FORMAT is set, --output-format and value appear in argv
def test_output_format_passed_when_set(self, startup_env: dict) -> None:
    _run_entrypoint(startup_env, extra_env={"FLAUDE_OUTPUT_FORMAT": "stream-json"})
    argv_lines = startup_env["argv_file"].read_text().splitlines()
    assert "--output-format" in argv_lines
    assert "stream-json" in argv_lines

# Test: --verbose is added when stream-json is requested
def test_verbose_added_for_stream_json(self, startup_env: dict) -> None:
    _run_entrypoint(startup_env, extra_env={"FLAUDE_OUTPUT_FORMAT": "stream-json"})
    argv_lines = startup_env["argv_file"].read_text().splitlines()
    assert "--verbose" in argv_lines

# Test: --verbose is NOT added for non-stream-json formats
def test_verbose_not_added_for_json_format(self, startup_env: dict) -> None:
    _run_entrypoint(startup_env, extra_env={"FLAUDE_OUTPUT_FORMAT": "json"})
    argv_lines = startup_env["argv_file"].read_text().splitlines()
    assert "--output-format" in argv_lines
    assert "json" in argv_lines
    assert "--verbose" not in argv_lines

# Test: no --output-format when env var is unset
def test_no_output_format_when_unset(self, startup_env: dict) -> None:
    _run_entrypoint(startup_env)
    argv_lines = startup_env["argv_file"].read_text().splitlines()
    assert "--output-format" not in argv_lines

# Test: flag ordering — output format flags come before -- separator
def test_output_format_before_separator(self, startup_env: dict) -> None:
    _run_entrypoint(startup_env, extra_env={"FLAUDE_OUTPUT_FORMAT": "stream-json"})
    argv_lines = startup_env["argv_file"].read_text().splitlines()
    idx_fmt = argv_lines.index("--output-format")
    idx_sep = argv_lines.index("--")
    assert idx_fmt < idx_sep
```

#### 2. MachineConfig injection tests
**File**: `tests/test_startup_env.py`
**Changes**: Add to `TestMachineConfigTokenInjection` or create a new `TestMachineConfigOutputFormat` class.

```python
class TestMachineConfigOutputFormat:
    def test_output_format_in_payload_env(self) -> None:
        config = MachineConfig(
            claude_code_oauth_token="token", prompt="test",
            output_format="stream-json",
        )
        payload = build_machine_config(config)
        assert payload["config"]["env"]["FLAUDE_OUTPUT_FORMAT"] == "stream-json"

    def test_no_output_format_env_when_empty(self) -> None:
        config = MachineConfig(
            claude_code_oauth_token="token", prompt="test",
        )
        payload = build_machine_config(config)
        assert "FLAUDE_OUTPUT_FORMAT" not in payload["config"]["env"]
```

#### 3. Log pipeline JSON-string passthrough test
**File**: `tests/test_log_payload_parsing.py`
**Changes**: Add a test confirming that when a Fly log entry's `message` field contains a JSON string (Claude stream-json output), it's extracted as-is.

```python
def test_json_string_message_extracted_as_is() -> None:
    """When Claude outputs stream-json, each line is a JSON string.
    Fly wraps it as a string in the message field — verify it passes through."""
    claude_json = '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello"}]}}'
    raw = _fly_stdout("m-1", claude_json)
    entry = parse_log_entry(raw)
    assert entry is not None
    assert entry.message == claude_json
```

#### 4. Exit marker detection with mixed JSON/text logs
**File**: `tests/test_log_payload_parsing.py` (or `tests/test_exit_code_propagation.py`)

```python
def test_exit_marker_found_among_json_lines() -> None:
    """extract_exit_code_from_logs finds the marker even when preceding
    lines are JSON strings from stream-json output."""
    logs = [
        '{"type":"system","subtype":"init","session_id":"abc"}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Done"}]}}',
        '{"type":"result","subtype":"success","result":"Done"}',
        "[flaude] Claude Code exited with code 0",
        "[flaude:exit:0]",
    ]
    from flaude.runner import extract_exit_code_from_logs
    assert extract_exit_code_from_logs(logs) == 0
```

### Success Criteria:

#### Automated Verification:
- [ ] `python -m pytest tests/test_startup_env.py -x -v`
- [ ] `python -m pytest tests/test_log_payload_parsing.py -x -v`
- [ ] `python -m pytest tests/ -x` (full suite green)

---

## Phase 3: Docs

### Dependencies
- Requires Phase 2 complete (tests validate the feature works)

### Changes Required:

#### 1. Dockerfile env var comment
**File**: `flaude/Dockerfile`
**Changes**: Add `FLAUDE_OUTPUT_FORMAT` to the env var comment block (line 43).

```dockerfile
# Environment variables expected at runtime (set by Fly machine config):
#   CLAUDE_CODE_OAUTH_TOKEN  — auth token for Claude Code
#   GITHUB_USERNAME          — for git clone auth
#   GITHUB_TOKEN             — for git clone auth
#   FLAUDE_REPOS             — JSON array of repo URLs to clone
#   FLAUDE_PROMPT            — the prompt string to pass to Claude Code
#   FLAUDE_OUTPUT_FORMAT     — output format: "stream-json", "json", or omit for text
```

#### 2. Streaming docs
**File**: `docs/guide/streaming.md`
**Changes**: Add a section explaining how to enable structured JSON output.

Add after the "Getting the result" section:

```markdown
## Structured JSON output

By default, Claude Code outputs human-readable text. Set `output_format="stream-json"` to
receive structured NDJSON events instead — each line is a self-contained JSON object with a
`type` field:

| Type | Description |
|------|-------------|
| `system` | Session init, hook events |
| `assistant` | Complete assistant message with content blocks |
| `result` | Final result with `total_cost_usd`, `usage`, `duration_ms` |

```python
config = MachineConfig(
    claude_code_oauth_token="sk-ant-oat-...",
    prompt="Refactor the auth module",
    repos=["https://github.com/your-org/your-repo"],
    output_format="stream-json",
)

async with await run_with_logs(app_name, config) as stream:
    async for line in stream:
        import json
        event = json.loads(line)
        if event.get("type") == "result":
            print(f"Cost: ${event['total_cost_usd']:.4f}")
        elif event.get("type") == "assistant":
            print(event["message"]["content"])
```

Each line in the stream is a JSON string. The log pipeline does not parse it — callers
are responsible for `json.loads()` on each line.

!!! note
    Lines from `entrypoint.sh` (like `[flaude] Starting execution` and `[flaude:exit:0]`)
    are plain text, not JSON. A robust consumer should handle `json.JSONDecodeError` for
    these lines.
```

### Success Criteria:

#### Automated Verification:
- [ ] `mkdocs build` succeeds (if configured)

#### Manual Verification:
- [ ] Docs render correctly
- [ ] Dockerfile comment is accurate

---

## Testing Strategy

### Automated:
- Entrypoint argv tests confirm flag passing works correctly
- MachineConfig tests confirm env var injection
- Log pipeline tests confirm JSON strings pass through unchanged
- Exit marker tests confirm detection works with mixed JSON/text logs
- Full test suite remains green (backward compatibility)

### Manual Testing Steps:
1. Build a flaude image with the updated entrypoint
2. Run with `output_format="stream-json"` and verify each log line is parseable JSON
3. Run without `output_format` and verify plain text output is unchanged
4. Verify the `result` event contains `total_cost_usd` and `usage`
5. Verify `[flaude:exit:N]` marker is still detected after JSON output

## References

- Claude Code CLI help: `claude -p --help` documents `--output-format` choices: `text`, `json`, `stream-json`
- Stream-json requires `--verbose` flag (error without it)
- Existing test patterns: `tests/test_startup_env.py` mock claude binary approach
