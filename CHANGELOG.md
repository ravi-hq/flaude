# Changelog

All notable changes to flaude will be documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/ravi-hq/flaude/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ravi-hq/flaude/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ravi-hq/flaude/releases/tag/v0.1.0
