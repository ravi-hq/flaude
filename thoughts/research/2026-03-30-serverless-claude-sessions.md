---
date: 2026-03-30T09:30:00-04:00
researcher: Claude Code (team-research skill)
git_commit: 1c742ec92ce2199ba8985ce13c3db4baf10cf982
branch: main
repository: ravi-hq/flaude
topic: "Serverless Claude Sessions — suspend/resume Fly machines for persistent multi-turn sessions"
tags: [research, team-research, fly-io, sessions, claude-code, serverless]
status: complete
method: agent-team
team_size: 4
tracks: [fly-api, claude-sessions, architecture-gaps, state-persistence]
last_updated: 2026-03-30
last_updated_by: Claude Code
---

# Research: Serverless Claude Sessions

**Date**: 2026-03-30T09:30:00-04:00
**Researcher**: Claude Code (team-research)
**Git Commit**: [`1c742ec`](https://github.com/ravi-hq/flaude/commit/1c742ec92ce2199ba8985ce13c3db4baf10cf982)
**Branch**: `main`
**Repository**: ravi-hq/flaude
**Method**: Agent team (4 specialist researchers)

## Research Question

How can flaude support persistent multi-turn "serverless Claude sessions" where each session maps to a Fly machine that can be stopped between prompts and restarted on demand, preserving Claude Code's conversation state across turns?

## Summary

Serverless sessions are fully viable using a **stop/start model with Fly Volumes**. Claude Code's `claude -p --resume <session-id>` flag enables programmatic session continuation. A single Fly Volume at `/data` persists both the workspace and Claude's session transcripts (via `CLAUDE_CONFIG_DIR=/data/claude`). Machines are stopped (not destroyed) between prompts and restarted with updated env vars for the next prompt. Wake latency is ~2-3 seconds (cold boot). True VM suspend (~200ms) exists but is unnecessary since `claude -p` exits after each prompt — there's no running process to snapshot.

## Research Tracks

### Track 1: Fly Machine Suspend/Resume API & Fly Router
**Researcher**: fly-api-researcher
**Scope**: Fly Machines API, suspend/resume endpoints, Fly Router auto-wake, fly-replay routing

#### Findings:
1. **Fly suspend snapshots full VM state** — CPU registers, memory, open file handles saved via Firecracker snapshotting. Resume in ~100-250ms. However, requires ≤2GB RAM, no swap, no GPU, no schedule.
2. **Suspend API endpoint** — `POST /v1/apps/{app}/machines/{id}/suspend`. Wait with `GET .../wait?state=suspended&timeout=60`.
3. **Start/resume endpoint** — `POST /v1/apps/{app}/machines/{id}/start` handles both resume-from-suspend and restart-from-stop transparently.
4. **Self-suspend from inside the machine** — Via Unix socket `/.fly/api`, no auth token needed: `curl --unix-socket /.fly/api -X POST http://flaps/v1/apps/$FLY_APP_NAME/machines/$FLY_MACHINE_ID/suspend`. Env vars `FLY_APP_NAME` and `FLY_MACHINE_ID` available in every machine.
5. **Fly Router auto-wake** — `auto_start_machines = true` + `auto_stop_machines = "stop"` in fly.toml. Only works through Fly Proxy — `.internal` DNS bypasses it. Use **Flycast** (`<appname>.flycast`) for private network auto-wake.
6. **fly-replay header routes to specific machines** — `fly-replay: instance=<machine_id>` forces routing. `prefer_instance=` adds fallback. Adds ~10ms latency. Requests over 1MB cannot be replayed.
7. **Billing** — Suspended and stopped machines cost the same: storage-only, no CPU/RAM charges.
8. **Suspend snapshots are single-use** — A snapshot can only be resumed once (security). Invalidated by deploys, host migrations, maintenance. Falls back to cold boot.
9. **Suspend is wrong for flaude's -p model** — Since `claude -p` exits after each prompt, there's no running process to suspend. Stop+volume is the correct approach.

### Track 2: Claude Code Session Continuity
**Researcher**: claude-session-researcher
**Scope**: Claude Code CLI flags, session storage, resume mechanics

#### Findings:
1. **`--resume <session-id>` resumes a specific session by UUID** — Works with `-p` (non-interactive) mode: `claude -p --resume <id> "prompt"` sends a follow-up and exits.
2. **`--continue` resumes most recent session in current directory** — No session ID needed, but less precise for multi-session use.
3. **Session transcripts stored at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`** — `<encoded-cwd>` replaces non-alphanumeric chars with `-` (e.g., `/workspace` → `-workspace`).
4. **Sessions are path-tied** — Can ONLY be resumed from the same absolute working directory. Different path = session not found. This is a hard constraint (anthropics/claude-code#5768).
5. **`CLAUDE_CONFIG_DIR` env var redirects all storage** — Set `CLAUDE_CONFIG_DIR=/data/claude` and transcripts write to `/data/claude/projects/...` instead of `~/.claude/projects/...`. No symlinks needed. Live-tested and confirmed.
6. **`--session-id <uuid>` pre-assigns session UUID** — Caller can control the ID from the start instead of parsing it from output.
7. **`--output-format json` returns session_id in response** — Machine-readable output includes the session ID for programmatic extraction.
8. **`--fork-session` creates a branch** — When used with `--resume`, copies history into a new session UUID. Useful for exploratory divergence.
9. **`--no-session-persistence` disables saving** — For stateless one-shot runs. Existing flaude behavior could use this to avoid writing orphaned transcripts.
10. **Cross-machine session resume not yet supported** — anthropics/claude-code#31992 is an open feature request. Our volume-based approach works around this by keeping the same machine.
11. **JSONL records include**: `type`, `sessionId`, `cwd`, `uuid`, `parentUuid`, `timestamp`, `gitBranch`, `version`, `message`. File-history snapshots track file backup state for undo.
12. **`~/.claude/history.jsonl` + `~/.claude/sessions/`** — Global index and session metadata files. Both must persist for `--resume` and session picker to work.

### Track 3: Flaude Architecture Gap Analysis
**Researcher**: architecture-analyst
**Scope**: All flaude source files — identifying changes needed for session support

#### Findings:
1. **`runner.py` — Destroy is unconditional** — `run()` wraps everything in `try/finally` that always calls `_cleanup_machine()` (stop + destroy). Session mode needs to stop without destroying. (`runner.py:286-316`)
2. **`machine_config.py` — `auto_destroy=True` default** — `build_machine_config()` always passes `config.auto_destroy` which defaults to `True`. Session machines need `False`. Already a field — just needs to be overridden by callers. (`machine_config.py:19,99,155`)
3. **`machine.py` — Missing start/update operations** — Only has `create_machine`, `stop_machine`, `destroy_machine`, `get_machine`. Needs `start_machine()` and `update_machine()` (PUT to update env vars on stopped machine). (`machine.py`)
4. **`entrypoint.sh` — One-shot `claude -p`, no session support** — Runs `claude -p -- "$FLAUDE_PROMPT"` and exits. Needs `FLAUDE_SESSION_ID` support to pass `--resume`, skip cloning if workspace exists, and emit `[flaude:session:<id>]` marker. (`entrypoint.sh:109-140`)
5. **`machine_config.py` — No volume/mount support** — `build_machine_config()` produces no `mounts` in the payload. Needs a `mounts` field for attaching Fly Volumes. (`machine_config.py:105-169`)
6. **No session registry** — No concept of a "session" in the codebase. Need a `Session` dataclass mapping `session_id → machine_id → volume_id → app_name` for the caller to persist.

### Track 4: State Persistence & Storage
**Researcher**: storage-researcher
**Scope**: Fly Volumes, state survival, billing, wake latency

#### Findings (partial — synthesized from cross-team collaboration):
1. **Fly Volumes survive machine stop/start** — Volume data persists across stop/start cycles. This is the foundation of the session model.
2. **Single volume layout confirmed** — Mount at `/data` with `/data/claude/` (CLAUDE_CONFIG_DIR) + `/data/workspace/` (code/repos).
3. **Volume API** — `POST /v1/apps/{app}/volumes` to create, `DELETE /v1/apps/{app}/volumes/{id}` to destroy. Volumes are region-locked (must match machine region).
4. **Stopped machine billing** — Storage charges only (volume GB), no CPU/RAM. Same as suspended.
5. **Wake latency** — Cold boot from stopped: ~2-3 seconds. Resume from suspend: ~100-250ms. Since stop/start is the model, expect 2-3s per prompt wake.

## Cross-Track Discoveries

- **Suspend is unnecessary for flaude's current model**: `claude -p` exits after each prompt, so there's no running process to snapshot. Stop + volume preserves all needed state (session transcripts on disk). Suspend becomes relevant only if flaude moves to long-lived interactive Claude processes.
- **`CLAUDE_CONFIG_DIR` is the linchpin**: Without it, session transcripts write to ephemeral `~/.claude/` inside the container and are lost on stop. This single env var makes the entire architecture work by redirecting storage to the Fly Volume.
- **`update_machine()` (PUT) enables prompt injection into stopped machines**: Rather than passing the new prompt via some external channel, update the machine's env vars (`FLAUDE_PROMPT`, `FLAUDE_SESSION_ID`) while stopped, then start it. The entrypoint reads env vars fresh on each boot.
- **Path-tied sessions + fixed mount = compatible**: Claude's hard requirement that sessions resume from the same absolute path is satisfied by always mounting the volume at `/data/workspace` and running Claude from there.
- **`--session-id <uuid>` simplifies first-turn flow**: Pre-assign the session UUID on first turn (via env var) so the caller controls the ID from the start, eliminating the need to parse it from output.

## Confirmed Architecture

### Volume Layout
```
/data/                          ← Fly Volume mount point
  claude/                       ← CLAUDE_CONFIG_DIR=/data/claude
    projects/
      -data-workspace/          ← encoded cwd for /data/workspace
        <session-id>.jsonl      ← session transcripts
    sessions/                   ← session metadata
    history.jsonl               ← global index
  workspace/                    ← git repos, user code
    <repo>/                     ← cloned on first turn only
```

### Session Lifecycle
```
First Turn:
  1. Create Fly Volume in target region
  2. Create machine (auto_destroy=False, volume mounted at /data)
     Env: FLAUDE_PROMPT, FLAUDE_SESSION_ID (pre-assigned UUID),
          CLAUDE_CONFIG_DIR=/data/claude
  3. Entrypoint: clone repos → cd /data/workspace → claude -p --session-id $ID -- "$PROMPT"
  4. Wait for exit → stop machine (not destroy)
  5. Return session_id + machine_id + volume_id to caller

Subsequent Turns:
  1. Update machine env vars (PUT): new FLAUDE_PROMPT, FLAUDE_SESSION_ID
  2. Start machine
  3. Entrypoint: skip clone (workspace exists) → cd /data/workspace → claude -p --resume $ID -- "$PROMPT"
  4. Wait for exit → stop machine
  5. Return result to caller

Session Destroy:
  1. Destroy machine
  2. Destroy volume (or keep for archival)
```

### Code Changes Required

| File | Change | Complexity |
|------|--------|-----------|
| `fly_client.py` | Add `fly_put()` method | Trivial |
| `machine.py` | Add `start_machine()`, `update_machine()` | Small |
| `machine_config.py` | Add `mounts` field, `session_id` field | Small |
| `runner.py` | Add `run_session_turn()` — stop instead of destroy, accept existing `machine_id` | Medium |
| `entrypoint.sh` | `FLAUDE_SESSION_ID` support, skip clone if workspace exists, `CLAUDE_CONFIG_DIR` | Small |
| *(new)* `volume.py` | `create_volume()`, `destroy_volume()` | Small |
| *(new)* `session.py` | `Session` dataclass (session_id, machine_id, volume_id, app_name, state) | Small |

### What Stays the Same
- `lifecycle.py` / `StreamingRun` — log streaming works per-turn as-is
- `executor.py` — concurrency layer unchanged
- `fly_client.py` base HTTP methods — just adding wrappers
- Existing `run()` / `run_and_destroy()` — one-shot mode unaffected

## Open Questions

1. **Session cleanup policy** — How long to keep stopped machines + volumes before destroying? Timer-based? Explicit destroy only?
2. **Volume size** — What default size for session volumes? Claude transcripts are small (KB), but git repos could be large.
3. **Concurrent access** — What happens if two prompts hit the same session simultaneously? Need a lock or queue.
4. **Session migration** — If the machine's host is decommissioned, Fly may move it. Does the volume follow? (Volumes are region-locked, so yes within region.)
5. **Auth token refresh** — `CLAUDE_CODE_OAUTH_TOKEN` may expire between turns. How to handle rotation?

## Related Research

*(No prior research documents in thoughts/research/)*
