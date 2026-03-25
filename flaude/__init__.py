# flaude: On-demand Claude Code execution on Fly.io

from flaude.app import FlyApp, ensure_app
from flaude.executor import (
    BatchResult,
    ConcurrentExecutor,
    ExecutionRequest,
    ExecutionResult,
)
from flaude.lifecycle import StreamingRun, run_with_logs
from flaude.log_drain import (
    LogCollector,
    LogDrainServer,
    LogEntry,
    LogStream,
    async_iter_queue,
    drain_queue,
    parse_log_entry,
    parse_ndjson,
)
from flaude.image import (
    ImageBuildError,
    docker_build,
    docker_login_fly,
    docker_push,
    ensure_image,
)
from flaude.machine import (
    FlyMachine,
    create_machine,
    destroy_machine,
    get_machine,
    stop_machine,
)
from flaude.machine_config import MachineConfig, RepoSpec, build_machine_config
from flaude.runner import (
    MachineExitError,
    RunResult,
    run,
    run_and_destroy,
    wait_for_machine_exit,
)

__all__ = [
    "BatchResult",
    "ConcurrentExecutor",
    "ExecutionRequest",
    "ExecutionResult",
    "FlyApp",
    "FlyMachine",
    "ImageBuildError",
    "LogCollector",
    "LogDrainServer",
    "LogEntry",
    "LogStream",
    "MachineConfig",
    "MachineExitError",
    "RepoSpec",
    "RunResult",
    "StreamingRun",
    "async_iter_queue",
    "build_machine_config",
    "create_machine",
    "destroy_machine",
    "docker_build",
    "docker_login_fly",
    "docker_push",
    "drain_queue",
    "ensure_app",
    "ensure_image",
    "get_machine",
    "parse_log_entry",
    "parse_ndjson",
    "run",
    "run_and_destroy",
    "run_with_logs",
    "stop_machine",
    "wait_for_machine_exit",
]
