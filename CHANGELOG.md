# Changelog

All notable changes to flaude will be documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.0] - 2026-03-31

### Fixed
- Claude Code tools silently denied in headless mode — added `--dangerously-skip-permissions` to entrypoint so tools are auto-approved in the ephemeral sandbox

## [0.5.0] - 2026-03-30

### Added
- Workspace file manifest: after repo cloning, `entrypoint.sh` emits a `[flaude:manifest:{...}]` tagged log line listing all workspace files
- `RunResult.workspace_files` field — tuple of relative file paths extracted from the manifest log line
- `extract_workspace_manifest_from_logs()` public utility for parsing manifest markers from collected logs
- `StreamingRun.result()` automatically populates `workspace_files` on the returned `RunResult`

## [0.4.0] - 2026-03-30

### Fixed
- Multi-turn sessions losing all conversation context from the first turn — working directory path mismatch caused `--resume` to never fire on turn 2+
- Persist effective CWD to `/data/.flaude_cwd` after first turn, restore on resume

## [0.3.0] - 2026-03-30

### Added
- Persistent multi-turn serverless sessions (`create_session`, `run_session_turn`, `destroy_session`)
- `Session` dataclass tracking machine + volume pairs across conversation turns
- `FlyVolume` model and volume CRUD (`create_volume`, `list_volumes`, `destroy_volume`)
- `start_machine()` and `update_machine()` for session machine lifecycle
- `fly_put()` HTTP method in Fly client
- Session-aware entrypoint with `--resume`/`--session-id` flag support
- `FLAUDE_SESSION_ID` and `CLAUDE_CONFIG_DIR` env var support for persistent conversations
- Volume mount support in `MachineConfig` (`volume_id`, `volume_mount_path`, `session_id`)
- Conditional repo cloning (skip on session resume when workspace is populated)

## [0.2.0] - 2026-03-28

### Added
- Structured JSON output format support (`output_format="stream-json"` in `MachineConfig`)
- `FLAUDE_OUTPUT_FORMAT` env var for container-level output format control
- `--verbose` flag auto-added when using `stream-json` format
- `/release` skill for interactive version releases via Claude Code
- `scripts/release.sh` and `Makefile` targets for terminal-based releases
- Release documentation in CONTRIBUTING.md

### Changed
- Public API surface tiered and reorganized
- Documentation overhauled: Overview page, llms.txt, project memo, image refs fixed

### Fixed
- `DEFAULT_IMAGE` registry mismatch
- `RunResult.destroyed` field behavior

## [0.1.0] - 2026-03-25

### Added
- Initial release
- `MachineConfig` for configuring Fly.io machine execution parameters
- `run_and_destroy()` for fire-and-forget prompt execution
- `run_with_logs()` for streaming log output
- `ensure_app()` for idempotent Fly.io app creation
- Automatic machine cleanup via `try/finally` guarantee
- Support for cloning multiple repos into `/workspace`
- Concurrent execution support

[Unreleased]: https://github.com/ravi-hq/flaude/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/ravi-hq/flaude/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/ravi-hq/flaude/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/ravi-hq/flaude/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ravi-hq/flaude/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ravi-hq/flaude/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ravi-hq/flaude/releases/tag/v0.1.0
