"""Focused tests for the local OpenCode CLI adapter.

No model download, network access or real OpenCode binary is required.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.executors import OpenCodeExecutor
from bridge.security import ProcessResult
from bridge.state_store import StateStore, TaskState


class OpenCodeExecutorTests(unittest.TestCase):
    def test_uses_noninteractive_run_with_explicit_model(self):
        executor = OpenCodeExecutor(model="llama.cpp/qwen-local")
        self.assertEqual(
            executor._build_argv("opencode", "fix bug"),
            ["opencode", "run", "--model", "llama.cpp/qwen-local", "fix bug"],
        )

    def test_rejects_missing_model_instead_of_using_cloud_default(self):
        with patch.dict("os.environ", {"OPENCODE_MODEL": ""}):
            executor = OpenCodeExecutor()
            with self.assertRaisesRegex(ValueError, "model not configured"):
                executor._build_argv("opencode", "fix bug")

    def test_runs_in_authorized_workspace_and_records_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            executor = OpenCodeExecutor(
                opencode_binary_path=sys.executable,  # existing binary; execution mocked
                model="llama.cpp/qwen-local",
                allowed_roots=[ws],
            )
            payload = {
                "schema_version": "1.0.0",
                "context": {
                    "session_id": "b0e8e333-1f9a-4e97-9701-567446c21a1d",
                    "source": "browser_extension",
                    "user_intent": "Fix the kernel test failure",
                    "decision_rationale": "Explicit user-approved test assignment",
                    "timestamp": "2026-10-17T20:00:00Z",
                },
                "task": {
                    "task_id": "qwen-executor-test",
                    "title": "Fix kernel test",
                    "description": "Investigate the kernel test and report changes.",
                    "category": "bugfix",
                    "priority": "medium",
                    "complexity": "small",
                },
                "constraints": {
                    "environment": {"os": "linux", "runtime": "local", "cwd": str(ws)},
                    "security_policy": {
                        "execution_mode": "sandboxed",
                        "permitted_commands": ["^git\\b"],
                        "forbidden_commands": [],
                        "allowed_file_paths": ["*"],
                        "forbidden_file_paths": [],
                    },
                    "limits": {
                        "max_execution_timeout_seconds": 30,
                        "max_iterations": 5,
                        "max_file_modifications": 5,
                    },
                },
                "file_operations": [],
                "verification": {
                    "automated_checks": [],
                    "acceptance_statement": "Tests compile and pass",
                },
            }
            store = StateStore(":memory:")
            try:
                result = ProcessResult(exit_code=0, stdout="ok\n", stderr="", duration_seconds=0.1)
                with patch.object(executor.process_runner, "run", return_value=result) as run:
                    outcome = executor.execute(payload, store)
                self.assertEqual(outcome["state"], TaskState.SUCCEEDED)
                argv = run.call_args.kwargs["argv"]
                self.assertEqual(argv[:4], [sys.executable, "run", "--model", "llama.cpp/qwen-local"])
                self.assertIn("Fix kernel test", argv[-1])
                self.assertEqual(Path(run.call_args.kwargs["cwd"]).resolve(), ws.resolve())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
