# flaude: On-demand Claude Code execution on Fly.io

# --- Primary API (stable) ---
from flaude.app import ensure_app
from flaude.executor import ConcurrentExecutor, ExecutionRequest
from flaude.lifecycle import run_with_logs
from flaude.machine_config import MachineConfig
from flaude.runner import RunResult, run_and_destroy

# --- Advanced API (public, may change in 0.x) ---
from flaude.app import FlyApp, create_app, get_app
from flaude.executor import BatchResult, ExecutionResult
from flaude.fly_client import fetch_machine_logs
from flaude.image import (
    ImageBuildError,
    docker_build,
    docker_login_fly,
    docker_push,
    ensure_image,
)
from flaude.lifecycle import StreamingRun
from flaude.log_drain import LogCollector, LogDrainServer, LogEntry, LogStream
from flaude.machine import (
    FlyMachine,
    create_machine,
    destroy_machine,
    get_machine,
    stop_machine,
)
from flaude.machine_config import RepoSpec, build_machine_config
from flaude.runner import MachineExitError, extract_exit_code_from_logs

__all__ = [
    # Primary API
    "ConcurrentExecutor",
    "ExecutionRequest",
    "MachineConfig",
    "RunResult",
    "ensure_app",
    "run_and_destroy",
    "run_with_logs",
    # Advanced API
    "BatchResult",
    "ExecutionResult",
    "FlyApp",
    "FlyMachine",
    "ImageBuildError",
    "LogCollector",
    "LogDrainServer",
    "LogEntry",
    "LogStream",
    "MachineExitError",
    "RepoSpec",
    "StreamingRun",
    "build_machine_config",
    "create_app",
    "create_machine",
    "destroy_machine",
    "docker_build",
    "docker_login_fly",
    "docker_push",
    "ensure_image",
    "extract_exit_code_from_logs",
    "fetch_machine_logs",
    "get_app",
    "get_machine",
    "stop_machine",
]
