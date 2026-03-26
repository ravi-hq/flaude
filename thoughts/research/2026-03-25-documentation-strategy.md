---
date: 2026-03-25T18:25:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 0de7ce314f2963d33e5f8ab7b5b7eb2a6b1d1cf3
branch: main
repository: local
topic: "Documentation strategy for flaude — tooling, structure, and hosting for Pythonista-native docs"
tags: [research, team-research, documentation, mkdocs, readthedocs, python]
status: complete
method: agent-team
team_size: 4
tracks: [ecosystem, api-audit, content-structure, tooling-setup]
last_updated: 2026-03-25
last_updated_by: Claude Code
---

# Research: Documentation Strategy for flaude

**Date**: 2026-03-25
**Researcher**: Claude Code (team-research)
**Git Commit**: `0de7ce3`
**Branch**: `main`
**Method**: Agent team (4 specialist researchers)

## Research Question

We want to build tools on top of this library. We need very clear docs around the functionality, preferably in a way that is native to Pythonistas. Maybe ReadTheDocs or something.

## Summary

The recommended documentation stack for flaude is **MkDocs + Material for MkDocs + mkdocstrings-python + GitHub Pages**. This matches the ecosystem flaude lives in (httpx, respx, hatch all use this exact stack), works natively with flaude's existing Google-style docstrings, and requires minimal setup (~50 lines of config). The existing codebase has solid docstring coverage on high-level functions but gaps in dataclass attributes and log infrastructure classes that should be filled before generating API reference docs. The documentation should be structured as: Getting Started (prerequisites + first run) -> Guides (one per execution mode + error handling) -> Concepts (architecture + lifecycle) -> API Reference (auto-generated, organized by concept not module).

## Research Tracks

### Track 1: Python Documentation Ecosystem
**Researcher**: ecosystem-researcher
**Scope**: Sphinx vs MkDocs vs alternatives, peer library analysis, hosting options

#### Findings:
1. **MkDocs + Material is the modern consensus for small Python libraries** — simpler than Sphinx, Markdown-native, faster to set up. Material for MkDocs v9.7.1 (Feb 2026) is the dominant theme with 60+ languages and built-in search.
2. **mkdocstrings-python v1.0.2 (Jan 2026) handles async natively** — uses Griffe under the hood with explicit `inspect_coroutine` support. Google/NumPy/Sphinx docstring styles all work out of the box.
3. **Peer library ecosystem split** — older async libs (trio, anyio, nox) use Sphinx+ReadTheDocs; newer httpx-orbit libs (httpx, respx, hatch) use MkDocs+GitHub Pages. flaude is squarely in the httpx orbit (httpx is its only dependency).
4. **Sphinx is viable but higher friction** — Furo is the modern Sphinx theme (used by pip, attrs, black). sphinxcontrib-trio handles async docs. But requires conf.py, autoapi/autodoc+napoleon setup, more boilerplate.
5. **GitHub Pages over ReadTheDocs for v0.x** — no extra account, no versioned docs needed yet, same approach as httpx/respx/hatch. Can graduate to RTD later if multi-version support becomes necessary.
6. **Alternatives ruled out** — pdoc (API-only, no narrative pages), quartodoc (scientific Python focus, overkill), Sphinx+RTD (heavier setup for equivalent result at this scale).

### Track 2: API Surface & Docstring Audit
**Researcher**: api-auditor
**Scope**: All 8 Python modules in `flaude/`

#### Findings:
1. **38 public symbols in `__all__`** — complete API surface across 8 modules covering config, execution, results, infrastructure, and image management.
2. **Uniform Google-style docstrings** — all documented symbols use `Args:`/`Returns:`/`Raises:` sections. No NumPy or reStructuredText styles observed. This is ideal for mkdocstrings with zero config.
3. **Well-documented high-level functions** — `run`, `run_and_destroy`, `run_with_logs`, `create_machine`, `wait_for_machine_exit`, `ConcurrentExecutor`, `ensure_image`, `docker_build`, `drain_queue`, `LogStream` all have complete Google-style docstrings with params, returns, and raises.
4. **Gap: Dataclass attribute docs** — `FlyMachine` (6 fields) and `LogEntry` (6 fields) lack `Attributes:` sections. These will render as undocumented in auto-generated API reference.
5. **Gap: Log infrastructure classes** — `LogCollector` methods use informal descriptions rather than Google-style sections. `LogDrainServer.__init__` has no `Args:` section (4 params undocumented). `parse_log_entry` documents behavior inline but lacks formal `Args:` header.
6. **Gap: Error class attributes** — `ImageBuildError.__init__` params (`returncode`, `stderr`) are not in the class docstring. `MachineExitError` is well-documented by contrast.
7. **Gap: Minimal one-liner docstrings** — `get_app`, `stop_machine`, `parse_ndjson`, `async_iter_queue`, and all simple properties across `LogStream`/`StreamingRun`/`LogDrainServer` have one-line docstrings with no structured sections.
8. **One missing docstring** — `BatchResult.all_succeeded` property has no docstring at all.

### Track 3: Documentation Content Structure
**Researcher**: content-researcher
**Scope**: Peer library doc structures, content gap analysis, recommended navigation

#### Findings:
1. **httpx model is the template** — QuickStart -> Clients -> Async Support -> Advanced -> API Reference -> Contributing. flaude should mirror this beginner-to-expert progression.
2. **README is a solid skeleton, not a replacement for docs** — The README already covers three quick-start examples, API overview tables, MachineConfig field reference, and env vars. The docs site should expand on each section rather than duplicate.
3. **Critical missing content: prerequisites** — Users will be blocked without: Fly.io account creation, `flyctl` install, `FLY_API_TOKEN` generation, Claude Code OAuth token acquisition, GitHub PAT for private repos, Docker image setup.
4. **Three priority tutorials needed** — (a) "Your first flaude run" (FLY_API_TOKEN + ensure_app + run_and_destroy), (b) "Streaming Claude Code output" (run_with_logs + StreamingRun), (c) "Running prompts in parallel" (ConcurrentExecutor + BatchResult + partial failure handling).
5. **API reference should be organized by concept, not module** — Users think in terms of what they're doing (configuring, executing, handling results), not which .py file contains the code. Recommended groups: Configuration, Execution, Concurrent Execution, App & Machine Management, Log Infrastructure, Image Management, Errors & Results.
6. **"Building on flaude" guide is warranted** — flaude's users are tool builders (CI bots, code review pipelines, batch refactoring). A dedicated guide should cover: sharing LogDrainServer across machines, tagging with metadata, choosing between ConcurrentExecutor vs manual asyncio.gather, integrating into FastAPI/CLI tools.
7. **Architecture/concepts section needed** — No current explanation of: the log drain lifecycle, try/finally cleanup guarantee, exit code fallback chain (API -> log marker), or how Fly machines map to flaude abstractions.
8. **Troubleshooting content needed** — Machine stuck in non-terminal state, log drain not receiving lines, orphaned machines, exit code None, timeout tuning.

### Track 4: Tooling & Hosting Setup
**Researcher**: tooling-researcher
**Scope**: Concrete config files, CI/CD, deployment

#### Findings:
1. **pyproject.toml docs extras** — Add `[project.optional-dependencies] docs = ["mkdocs-material>=9.7", "mkdocstrings[python]>=0.28"]`. Only 2 packages needed.
2. **mkdocs.yml is ~30 lines** — Material theme with `navigation.tabs`, `navigation.sections`, `content.code.copy` features. mkdocstrings configured with `docstring_style: google`, `show_source: true`, `show_signature_annotations: true`.
3. **GitHub Actions deployment is 15 lines** — Single workflow: checkout -> setup-python -> pip install docs deps -> `mkdocs gh-deploy --force`. Deploys to `gh-pages` branch on every push to `main`. This is the official Material for MkDocs recommended pattern.
4. **Hatch integration via scripts** — Add `[tool.hatch.envs.docs]` with `serve = "mkdocs serve"`, `build = "mkdocs build --strict"`, `deploy = "mkdocs gh-deploy --force"`. Then `hatch run docs:serve` for local preview.
5. **docs/ directory structure** — 5 markdown files to start: `index.md` (home), `getting-started.md`, `guide.md` (user guide), `api.md` (API reference with `::: flaude.MachineConfig` directives), `changelog.md`.
6. **mkdocstrings API reference syntax** — Use `::: flaude.MachineConfig` directives in markdown files. Griffe auto-discovers docstrings, type annotations, and async signatures. No separate build step needed.
7. **CI doc build check** — `mkdocs build --strict` in CI catches broken references before deployment. Can be a nox session or hatch script.
8. **No versioned docs needed yet** — At v0.1.0, a single `main` branch deploy is sufficient. ReadTheDocs versioning can be added later if stable/dev splits become necessary.

## Cross-Track Discoveries

- **Docstring style determines tooling with zero friction** — The uniform Google-style docstrings across all modules mean mkdocstrings-python works with zero configuration for the `docstring_style` beyond setting it to `google`. No docstring migration needed.
- **README content can bootstrap the docs site** — The README's three examples, API tables, and config reference can be directly expanded into the Getting Started, User Guide, and API Reference pages rather than starting from scratch.
- **Gap between high-level and infrastructure docs** — High-level execution functions (`run`, `run_and_destroy`, `run_with_logs`) are well-documented; infrastructure components (`LogCollector`, `LogDrainServer`, `LogEntry`) are not. This maps to a docs site that has good "getting started" content but weak "building on flaude" content — exactly the gap the user wants to fill.

## Recommended Documentation Structure

```
docs/
  index.md                    # Home: what flaude is, install, 30-second example
  getting-started.md          # Prerequisites (Fly.io, tokens), first run tutorial
  guide/
    streaming.md              # run_with_logs + StreamingRun guide
    concurrent.md             # ConcurrentExecutor + BatchResult guide
    error-handling.md          # MachineExitError, FlyAPIError, timeouts
    private-repos.md           # RepoSpec, GitHub PAT setup
    docker-image.md            # ensure_image, custom builds
    building-on-flaude.md      # Advanced: shared log drains, metadata, CI integration
  concepts/
    architecture.md            # How flaude works, diagram, lifecycle
    log-drain.md               # Log infrastructure internals
  api/
    configuration.md           # MachineConfig, RepoSpec
    execution.md               # run, run_and_destroy, run_with_logs, StreamingRun
    concurrent.md              # ConcurrentExecutor, ExecutionRequest, BatchResult
    app-machine.md             # ensure_app, FlyApp, create_machine, FlyMachine
    log-infrastructure.md      # LogDrainServer, LogCollector, LogStream, LogEntry
    image.md                   # ensure_image, docker_build, docker_push
    errors.md                  # RunResult, MachineExitError, FlyAPIError, ImageBuildError
  changelog.md
```

## Concrete Config Files

### mkdocs.yml

```yaml
site_name: flaude
site_description: On-demand Claude Code execution on Fly.io
repo_url: https://github.com/<org>/flaude
repo_name: <org>/flaude

theme:
  name: material
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.expand
    - content.code.copy
    - content.code.annotate
    - search.suggest

plugins:
  - search
  - mkdocstrings:
      default_handler: python
      handlers:
        python:
          options:
            docstring_style: google
            show_source: true
            show_signature_annotations: true
            show_root_heading: true
            members_order: source

markdown_extensions:
  - admonition
  - pymdownx.details
  - pymdownx.superfences
  - pymdownx.tabbed:
      alternate_style: true
  - pymdownx.highlight:
      anchor_linenums: true

nav:
  - Home: index.md
  - Getting Started: getting-started.md
  - User Guide:
    - Streaming Logs: guide/streaming.md
    - Concurrent Execution: guide/concurrent.md
    - Error Handling: guide/error-handling.md
    - Private Repos: guide/private-repos.md
    - Docker Image: guide/docker-image.md
    - Building on flaude: guide/building-on-flaude.md
  - Concepts:
    - Architecture: concepts/architecture.md
    - Log Drain: concepts/log-drain.md
  - API Reference:
    - Configuration: api/configuration.md
    - Execution: api/execution.md
    - Concurrent: api/concurrent.md
    - App & Machine: api/app-machine.md
    - Log Infrastructure: api/log-infrastructure.md
    - Image Management: api/image.md
    - Errors & Results: api/errors.md
  - Changelog: changelog.md
```

### pyproject.toml additions

```toml
[project.optional-dependencies]
docs = [
    "mkdocs-material>=9.7",
    "mkdocstrings[python]>=0.28",
]

[tool.hatch.envs.docs]
features = ["docs"]

[tool.hatch.envs.docs.scripts]
serve = "mkdocs serve"
build = "mkdocs build --strict"
deploy = "mkdocs gh-deploy --force"
```

### .github/workflows/docs.yml

```yaml
name: docs
on:
  push:
    branches: [main]
permissions:
  contents: write
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install "mkdocs-material>=9.7" "mkdocstrings[python]>=0.28"
      - run: mkdocs gh-deploy --force
```

## Docstring Gaps to Fill Before Generating Docs

Priority fixes (these will show up as undocumented in auto-generated API reference):

| Symbol | File | Gap |
|--------|------|-----|
| `FlyMachine` | `machine.py:15` | Add `Attributes:` section for `id`, `name`, `state`, `region`, `instance_id`, `app_name` |
| `LogEntry` | `log_drain.py:38` | Add `Attributes:` section for `machine_id`, `message`, `stream`, `timestamp`, `app_name`, `raw` |
| `ImageBuildError` | `image.py:28` | Add `Attributes:` section for `returncode`, `stderr` |
| `LogDrainServer.__init__` | `log_drain.py:211` | Add `Args:` section for `collector`, `host`, `port`, `include_stderr` |
| `LogCollector` methods | `log_drain.py:50` | Convert informal descriptions to Google-style `Args:`/`Returns:` |
| `stop_machine` | `machine.py:148` | Expand one-liner to include `Args:` section |
| `get_app` | `app.py:33` | Expand one-liner to include `Args:`/`Returns:` |
| `BatchResult.all_succeeded` | `executor.py:77` | Add docstring |
| `parse_ndjson` | `log_drain.py:175` | Add `Args:`/`Returns:` |

## Open Questions

1. **GitHub org/repo URL** — The config templates use `<org>/flaude` as a placeholder. What is the actual GitHub repository URL for hosting and GitHub Pages deployment?
2. **Custom domain** — Should the docs live at `<org>.github.io/flaude/` or a custom domain like `flaude.dev`?
3. **Changelog format** — Use [Keep a Changelog](https://keepachangelog.com/) or auto-generate from git tags?
4. **When to add ReadTheDocs** — At what point (v1.0? multiple stable versions?) should versioned docs via RTD be considered?
