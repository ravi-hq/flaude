"""Tests for workspace manifest extraction from log lines."""

import json

from flaude.runner import extract_workspace_manifest_from_logs


class TestExtractWorkspaceManifest:
    """extract_workspace_manifest_from_logs — unit tests."""

    def test_extracts_files_from_manifest_marker(self):
        manifest = json.dumps({"workspace": "/workspace/myrepo", "files": ["./src/main.py", "./README.md"]})
        logs = [
            "[flaude] Starting execution",
            "[flaude] Cloning repos...",
            f"[flaude:manifest:{manifest}]",
            "Claude output here",
        ]
        assert extract_workspace_manifest_from_logs(logs) == ("./src/main.py", "./README.md")

    def test_returns_empty_tuple_when_no_marker(self):
        logs = ["[flaude] Starting execution", "some output"]
        assert extract_workspace_manifest_from_logs(logs) == ()

    def test_returns_empty_tuple_for_empty_logs(self):
        assert extract_workspace_manifest_from_logs([]) == ()

    def test_returns_empty_tuple_for_malformed_json(self):
        logs = ["[flaude:manifest:not-json]"]
        assert extract_workspace_manifest_from_logs(logs) == ()

    def test_returns_empty_tuple_for_missing_files_key(self):
        logs = ['[flaude:manifest:{"workspace":"/workspace"}]']
        assert extract_workspace_manifest_from_logs(logs) == ()

    def test_handles_large_file_list(self):
        files = [f"./src/file_{i}.py" for i in range(500)]
        manifest = json.dumps({"workspace": "/workspace", "files": files})
        logs = [f"[flaude:manifest:{manifest}]"]
        result = extract_workspace_manifest_from_logs(logs)
        assert len(result) == 500
        assert result[0] == "./src/file_0.py"

    def test_first_marker_wins(self):
        m1 = json.dumps({"workspace": "/w", "files": ["./a.py"]})
        m2 = json.dumps({"workspace": "/w", "files": ["./b.py"]})
        logs = [f"[flaude:manifest:{m1}]", f"[flaude:manifest:{m2}]"]
        assert extract_workspace_manifest_from_logs(logs) == ("./a.py",)

    def test_coexists_with_exit_marker(self):
        manifest = json.dumps({"workspace": "/workspace", "files": ["./main.py"]})
        logs = [
            f"[flaude:manifest:{manifest}]",
            "Claude output",
            "[flaude:exit:0]",
        ]
        assert extract_workspace_manifest_from_logs(logs) == ("./main.py",)


class TestRunResultWorkspaceFiles:
    """RunResult.workspace_files field."""

    def test_default_is_empty_tuple(self):
        from flaude.runner import RunResult
        r = RunResult(machine_id="m1", exit_code=0, state="stopped", destroyed=True)
        assert r.workspace_files == ()

    def test_accepts_workspace_files(self):
        from flaude.runner import RunResult
        r = RunResult(
            machine_id="m1", exit_code=0, state="stopped",
            destroyed=True, workspace_files=("./a.py", "./b.py"),
        )
        assert r.workspace_files == ("./a.py", "./b.py")
