---
date: 2026-03-25T18:25:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 0de7ce314f2963d33e5f8ab7b5b7eb2a6b1d1cf3
branch: main
repository: local
topic: "E2E validation automation for flaude workflows with real tokens"
tags: [research, team-research, e2e, testing, automation, fly-io]
status: complete
method: agent-team
team_size: 4
tracks: [lifecycle, container, test-patterns, token-management]
last_updated: 2026-03-25
last_updated_by: Claude Code
---

# Research: E2E Validation Automation for flaude

**Date**: 2026-03-25
**Researcher**: Claude Code (team-research)
**Git Commit**: `0de7ce3`
**Branch**: `main`
**Repository**: local
**Method**: Agent team (4 specialist researchers)

## Research Question

We need to validate that a flaude workflow runs with real tokens that start a Fly.io machine, clone from GitHub, and execute something with Claude Code. This should be as easy and automated as possible.

## Summary

flaude's existing test suite is 100% mocked — no test touches real Fly.io APIs, real Docker, or real Claude Code. The library's architecture already supports real token injection via explicit `token=` parameters and `MachineConfig` fields, so adding E2E tests requires no library changes. The minimal E2E test needs only 2 tokens (`FLY_API_TOKEN` + `CLAUDE_CODE_OAUTH_TOKEN`), no GitHub credentials, no repo cloning, and can validate the full lifecycle by checking for the `[flaude:exit:0]` marker in streamed logs. The recommended approach is a `pytest -m e2e` marker with skip-if-no-tokens fixtures.

## Research Tracks

### Track 1: Fly.io Machine Lifecycle & API
**Researcher**: lifecycle-researcher
**Scope**: `flaude/fly_client.py`, `flaude/machine.py`, `flaude/runner.py`, `flaude/lifecycle.py`, `flaude/app.py`

#### Findings:
1. **API call sequence** — Full lifecycle is: `ensure_app()` -> `POST /apps/{app}/machines` -> `GET .../wait?state=stopped` (long-poll) -> `GET .../machines/{id}` (exit code) -> `POST .../stop` -> `DELETE .../machines/{id}?force=true` (`runner.py:250-316`, `machine.py:94,160,192`)
2. **Terminal states** — `{"stopped", "destroyed", "failed"}` defined at `runner.py:25`
3. **Guaranteed cleanup** — try/finally in both `run()` (`runner.py:279-316`) and `_wait_signal_destroy()` (`lifecycle.py:178-228`); `_cleanup_machine` swallows stop errors and logs destroy failures
4. **Exit code extraction** — Three layers: Fly API events, top-level status, then `[flaude:exit:N]` log marker fallback (`runner.py:204-219`, `lifecycle.py:147-149`)
5. **Failure handling** — 404/409 on stop/destroy are silently swallowed; timeout propagates but cleanup still runs; creation failure skips cleanup safely (`machine.py:166-172,200-201`)
6. **Streaming path** — Log drain server starts BEFORE machine creation; collector signals sentinel on exit; background task handles destroy (`lifecycle.py:274-327`)
7. **Smoke test checkpoints** — Validate: app exists, machine created with non-empty ID, wait returns terminal state, exit code is 0, machine is destroyed

### Track 2: Container & Entrypoint Flow
**Researcher**: container-researcher
**Scope**: `flaude/Dockerfile`, `flaude/entrypoint.sh`, `flaude/image.py`

#### Findings:
1. **Docker image contents** — `node:22-bookworm-slim` base, git, curl, jq, gh CLI, Claude Code via `npm install -g @anthropic-ai/claude-code` (`Dockerfile:3,9-15,18-24,27-28`)
2. **Entrypoint flow** — Strictly ordered: validate CLAUDE_CODE_OAUTH_TOKEN -> clone repos -> validate FLAUDE_PROMPT -> cd workspace -> `claude -p -- "$FLAUDE_PROMPT"` -> capture exit code -> print markers (`entrypoint.sh:10-130`)
3. **Output markers for validation** — Key signals: `[flaude] Starting execution`, `[flaude] No repositories to clone`, `[flaude] Running Claude Code in <path> ...`, `[flaude] Claude Code exited with code N`, `[flaude:exit:N]` (canonical machine-readable marker at line 128)
4. **Git credentials** — Configured only when both GITHUB_USERNAME and GITHUB_TOKEN are set; writes `~/.git-credentials` (`entrypoint.sh:37-42`)
5. **Minimal verifiable prompt** — No repos needed; a prompt like `"Print only the word DONE"` produces grepable output between the `[flaude] Running Claude Code` and `[flaude:exit:0]` markers
6. **Image build pipeline** — `ensure_image()` orchestrates `docker_build()` -> `docker_login_fly()` -> `docker_push()` via `asyncio.create_subprocess_exec` (`image.py:100-239`)

### Track 3: Existing Test Patterns & Gaps
**Researcher**: test-researcher
**Scope**: `tests/` (20 test files), `pyproject.toml`

#### Findings:
1. **Framework** — pytest with `asyncio_mode = "auto"`, respx for HTTP mocking, no shared fixtures or conftest.py (`pyproject.toml:24-26`)
2. **Mocking pattern** — `@respx.mock` decorator + inline `respx.post/get/delete(url).mock(return_value=...)` per test; `side_effect` for sequential/stateful responses
3. **Token handling in tests** — All hardcoded: `"test-fly-token"`, `"test-oauth-token"` — no env var reading
4. **Zero real integration tests** — Every HTTP call is mocked. No test hits Fly.io APIs, builds Docker images, or creates real machines
5. **No custom markers** — No `e2e`, `integration`, `slow` markers defined; no conftest.py exists
6. **E2E readiness** — Library already supports explicit `token=` kwargs on all functions; E2E tests can inject real tokens without code changes
7. **Shell script tests** — `test_entrypoint.py`, `test_prompt_execution.py`, `test_startup_env.py` test entrypoint.sh via subprocess with mock binaries

### Track 4: Token & Credential Management
**Researcher**: token-researcher
**Scope**: `.env`, `flaude/machine_config.py`, `flaude/fly_client.py`, `.gitignore`

#### Findings:
1. **FLY_API_TOKEN** — Host-side only, read from env (`fly_client.py:27`), never forwarded to container; all APIs accept `token=` override
2. **CLAUDE_CODE_OAUTH_TOKEN** — Required, validated at config build time (`machine_config.py:118-119`), forwarded into container env, validated again by entrypoint
3. **GITHUB_USERNAME + GITHUB_TOKEN** — Optional, only needed for private repos; safely omittable for minimal E2E
4. **No dotenv loading** — Zero usage of python-dotenv in the codebase; `.env` must be manually sourced
5. **.env is gitignored** — `.gitignore` line 1; current `.env` has real FLY_API_TOKEN and GITHUB credentials (no CLAUDE_CODE_OAUTH_TOKEN)
6. **No token validation** — Only presence checks, no format/validity pre-flight; invalid tokens fail at runtime (401 from Fly API)
7. **Minimum for E2E** — Just `FLY_API_TOKEN` (env) + `CLAUDE_CODE_OAUTH_TOKEN` (MachineConfig field); no GitHub creds needed

## Cross-Track Discoveries

- **`[flaude:exit:N]` is the canonical contract** — Written by `entrypoint.sh:128`, parsed by `runner.py:28-53` and `lifecycle.py:147-149`. E2E tests should scan for this marker as the definitive success signal.
- **No library changes needed for E2E** — The explicit `token=` parameter pattern already exists on all public APIs. E2E tests just pass real tokens instead of mock strings.
- **Log drain works locally in tests** — `test_log_drain.py` already runs a real `LogDrainServer` on port 0. The streaming E2E test can reuse this pattern but with a real machine feeding it.
- **Simplest possible E2E** — `ensure_app()` + `run_and_destroy()` with `MachineConfig(prompt="Print DONE", claude_code_oauth_token=real_token)` and no repos. Validates: app creation, machine lifecycle, Claude Code execution, exit code propagation, and machine cleanup.

## Architecture: Recommended E2E Test Design

### Minimal setup needed:

```
pyproject.toml changes:
  markers = ["e2e: real Fly.io integration tests (require tokens)"]

New files:
  tests/conftest.py         — skip-if-no-tokens fixtures
  tests/test_e2e.py         — the actual E2E tests
```

### conftest.py fixtures:

1. `fly_token` — reads `FLY_API_TOKEN` from env, `pytest.skip()` if absent
2. `claude_token` — reads `CLAUDE_CODE_OAUTH_TOKEN` from env, `pytest.skip()` if absent
3. `e2e_app_name` — reads `FLAUDE_E2E_APP` from env or defaults to `"flaude-e2e-test"`
4. `e2e_app` — calls `ensure_app(e2e_app_name, token=fly_token)` (session-scoped)

### Test scenarios (in order of complexity):

1. **Smoke test** — `run_and_destroy()` with prompt `"Print the word DONE"`, no repos. Assert exit_code == 0.
2. **Streaming test** — `run_with_logs()` with same prompt. Assert logs contain `[flaude:exit:0]` and some Claude output.
3. **GitHub clone test** — Add `repos=["https://github.com/octocat/Hello-World"]` (public repo, no creds needed). Assert logs contain `[flaude] All 1 repositories cloned`.
4. **Private repo clone test** — Requires GITHUB_USERNAME + GITHUB_TOKEN. Assert clone success.
5. **Failure test** — Prompt that causes non-zero exit. Assert `MachineExitError` raised.

### Running:

```bash
# Skip E2E tests (default — no tokens in env):
pytest

# Run E2E tests:
FLY_API_TOKEN="..." CLAUDE_CODE_OAUTH_TOKEN="..." pytest -m e2e -v

# Run from .env:
source .env && CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat-..." pytest -m e2e -v
```

## Code References

| File | Tracks | Key Findings |
|------|--------|-------------|
| `flaude/fly_client.py:27-31` | 1, 4 | FLY_API_TOKEN reading + token= override pattern |
| `flaude/machine_config.py:118-132` | 2, 4 | Token validation + env var injection into machine payload |
| `flaude/runner.py:250-316` | 1 | run() with try/finally guaranteed cleanup |
| `flaude/runner.py:28-53` | 1, 2 | [flaude:exit:N] marker parsing |
| `flaude/lifecycle.py:231-327` | 1 | run_with_logs() streaming path with log drain setup |
| `flaude/entrypoint.sh:10-130` | 2 | Full entrypoint flow with all output markers |
| `flaude/entrypoint.sh:128` | 1, 2, 3 | Canonical [flaude:exit:N] marker |
| `flaude/image.py:100-239` | 2 | Docker build/push pipeline |
| `pyproject.toml:24-26` | 3 | pytest config (no markers, no conftest) |
| `.gitignore:1` | 4 | .env is gitignored |

## Open Questions

1. **Docker image availability** — Is `registry.fly.io/flaude:latest` already pushed? E2E tests need the image to exist. Should E2E setup call `ensure_image()` first, or assume it's pre-built?
2. **Cost/time** — Each E2E test spins up a real Fly machine (~$0.01-0.05 per run, 30-120s per test). Should there be a timeout cap or cost warning?
3. **Claude Code OAuth token source** — The `.env` file doesn't include `CLAUDE_CODE_OAUTH_TOKEN`. Where should testers get this token? Is there a service account token for CI?
4. **CI integration** — Should E2E tests run in CI (GitHub Actions)? If so, tokens need to be stored as repository secrets.
5. **Test app cleanup** — Should E2E tests create a fresh app per run and delete it after, or reuse a persistent `flaude-e2e-test` app?
