# flaude: On-demand Claude Code execution on Fly.io

# --- Primary API (stable) ---
# --- Advanced API (public, may change in 0.x) ---
from flaude.app import FlyApp, create_app, ensure_app, get_app
from flaude.executor import (
    BatchResult,
    ConcurrentExecutor,
    ExecutionRequest,
    ExecutionResult,
)
from flaude.fly_client import fetch_machine_logs
from flaude.image import (
    ImageBuildError,
    docker_build,
    docker_login_fly,
    docker_push,
    ensure_image,
)
from flaude.lifecycle import StreamingRun, run_with_logs
from flaude.log_drain import LogCollector, LogDrainServer, LogEntry, LogStream
from flaude.machine import (
    FlyMachine,
    create_machine,
    destroy_machine,
    get_machine,
    start_machine,
    stop_machine,
    update_machine,
)
from flaude.machine_config import MachineConfig, RepoSpec, build_machine_config
from flaude.runner import (
    MachineExitError,
    RunResult,
    extract_exit_code_from_logs,
    extract_workspace_manifest_from_logs,
    run_and_destroy,
    run_session_turn,
)
from flaude.session import Session, create_session, destroy_session
from flaude.volume import FlyVolume, create_volume, destroy_volume, list_volumes

__all__ = [
    # Primary API
    "ConcurrentExecutor",
    "ExecutionRequest",
    "MachineConfig",
    "RunResult",
    "Session",
    "create_session",
    "destroy_session",
    "ensure_app",
    "run_and_destroy",
    "run_session_turn",
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
    "extract_workspace_manifest_from_logs",
    "fetch_machine_logs",
    "get_app",
    "FlyVolume",
    "create_volume",
    "destroy_volume",
    "get_machine",
    "list_volumes",
    "start_machine",
    "stop_machine",
    "update_machine",
]
