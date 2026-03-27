# TODOS

## Log Drain Setup Documentation

**What:** Add a "Log Streaming Setup" section to the docs explaining how to configure
Fly.io's NDJSON log drain to point at the `LogDrainServer`.

**Why:** `run_with_logs()` starts an HTTP server that receives logs, but Fly.io needs
to be told where to send them. Without this documentation, a user calling `run_with_logs()`
will get an empty log stream with no error message. This is the #1 support question waiting
to happen.

**Pros:** Prevents silent failure for every new user of log streaming. Completes the
"10 minutes from pip install to working" success criterion.

**Cons:** Adds a setup step to the quick-start flow. May require the user to have a
publicly reachable endpoint (or run within Fly's network).

**Context:** Found during /plan-eng-review outside voice challenge (2026-03-26). The
`LogDrainServer` binds 0.0.0.0 and accepts NDJSON POST requests. Fly.io supports
HTTP log drains at the app level via `flyctl` or the API. The library handles the
receiving side; the user must configure the sending side.

**Depends on / blocked by:** Nothing. Can be done independently.

**Added:** 2026-03-26 | **Source:** /plan-eng-review outside voice
