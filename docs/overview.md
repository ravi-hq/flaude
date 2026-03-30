# Overview

## The problem

Running Claude Code locally works for one-off tasks. But if you want to run it programmatically, on remote infrastructure, against repos you don't have cloned locally, or run many prompts in parallel, you're on your own.

Without flaude, you'd need to:

1. Write Fly.io API calls to create and manage machines
2. Build and push a Docker image with Claude Code pre-installed
3. Handle machine lifecycle (create, wait, poll for exit, destroy)
4. Parse exit codes from Fly's event API (which has three different response formats)
5. Set up log streaming via HTTP log drains and NDJSON parsing
6. Guarantee cleanup on every code path: success, failure, timeout, cancellation

flaude packages all of that into `pip install flaude` and a few lines of Python.

## What flaude gives you

- **`run_and_destroy()`** — fire-and-forget prompt execution. Create a VM, run Claude Code, get the result, destroy the VM. One function call.
- **`run_with_logs()`** — same thing, but with real-time log streaming via async iteration. Watch Claude Code think while it works.
- **`create_session()` / `run_session_turn()`** — persistent multi-turn conversations. The machine stops between prompts instead of being destroyed, preserving the full conversation and workspace on a Fly Volume.
- **`ConcurrentExecutor`** — run many prompts in parallel with configurable concurrency limits. Get back a `BatchResult` with per-request outcomes.
- **Guaranteed cleanup** — machines are always destroyed via `try/finally`, even on exceptions, cancellation, or timeouts.
- **Single dependency** — just [httpx](https://www.python-httpx.org/). The log drain server uses stdlib asyncio. No ASGI frameworks, no extra event loops.

## What you can build with it

flaude is a primitive. It's the foundation layer that handles the infrastructure so you can focus on what you're building on top.

**CI integration** — a test fails, flaude spins up Claude Code to analyze the failure and propose a fix. Or run Claude Code against every PR for automated code review. There's a [GitHub Actions example](guide/building-on-flaude.md#ci-integration-github-actions) in the guides.

**Batch processing** — run the same prompt against dozens or hundreds of repos. Migrate a codebase to a new pattern. Add type annotations to every file. Audit security across your org.

**Interactive agents** — use sessions to build agents that have multi-turn conversations with Claude Code. Ask it to analyze a codebase, then follow up with fixes, then verify — all in one persistent session with full context retention.

**Multi-agent orchestration** — spin up N machines that each work on a different part of a codebase, then combine the results. Divide and conquer at the infrastructure level.

**Scheduled maintenance** — a cron job that runs Claude Code against your repo weekly to find tech debt, outdated dependencies, or missing test coverage.

**Internal tooling** — wrap flaude in a web service and give your team a "run Claude Code against this repo" button. The library handles all the Fly.io machinery.

## How it works

```
Your code                    Fly.io
───────                      ──────
MachineConfig ──► create VM ──► clone repos
                                  │
                              run Claude Code
                                  │
            ◄── stream logs ◄─ stdout/stderr
                                  │
              destroy VM ◄──── exit
```

1. A Docker container with Claude Code, git, and the GitHub CLI boots on Fly.io
2. The entrypoint clones your specified repos into `/workspace`
3. Claude Code runs your prompt in print mode (`-p`)
4. Logs stream back to your process via HTTP log drains (NDJSON)
5. The machine is always destroyed after completion (guaranteed via `try/finally`)

For a deeper look at the internals, see [Architecture](concepts/architecture.md).

## Next steps

- [Getting Started](getting-started.md) — prerequisites and your first run
- [Multi-Turn Sessions](guide/sessions.md) — persistent conversations across prompts
- [Streaming Logs](guide/streaming.md) — watch Claude Code output in real time
- [Concurrent Execution](guide/concurrent.md) — run many prompts at once
- [Building on flaude](guide/building-on-flaude.md) — CI integration, shared log drains, and more
