"""Unit and integration tests for MockAgentExecutor, PIAgentExecutor, and TaskRunnerPool.

Covers:
- BaseAgentExecutor abstraction
- MockAgentExecutor:
    - file operations: create, modify, delete, rename, inspect
    - dry_run simulation mode
    - interactive approval gating
    - path traversal detection and jailbreak defense
    - automated checks: file_exists, file_contains, file_matches_regex, command assertions
    - non-blocking vs blocking verification failures
- PIAgentExecutor:
    - prompt formatting and instruction compilation
    - process execution and error handling
    - dry_run mode and CLI binary resolution
- TaskRunnerPool:
    - task queuing and worker thread concurrency
    - active task counting and cancellation
    - event broadcasting and subscriber callbacks
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.executors import MockAgentExecutor, PIAgentExecutor, TaskRunnerPool
from bridge.security import ProcessResult
from bridge.state_store import StateStore, TaskState


class TestMockAgentExecutor(unittest.TestCase):
    """Test suite for MockAgentExecutor file operations and verification checks."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.store = StateStore(":memory:")
        self.executor = MockAgentExecutor(allowed_roots=[self.workspace_root])

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def _build_payload(self, task_id="task-mock-01", file_ops=None, checks=None, mode="sandboxed"):
        return {
            "schema_version": "1.0.0",
            "context": {
                "session_id": "c0eebc99-9c0b-4ef8-bb6d-6bb9bd380a99",
                "source": "chatgpt_web_action",
                "user_intent": "Test mock executor",
                "decision_rationale": "Verify file modifications and automated checks",
                "timestamp": "2026-09-01T04:00:00Z",
            },
            "task": {
                "task_id": task_id,
                "title": "Mock Executor Test Task",
                "description": "Execute mock steps on local filesystem.",
                "category": "feature",
                "priority": "high",
                "complexity": "small",
            },
            "constraints": {
                "environment": {
                    "os": "mac",
                    "runtime": "python>=3.11",
                    "cwd": str(self.workspace_root),
                },
                "security_policy": {
                    "execution_mode": mode,
                    "permitted_commands": ["python3", "echo"],
                    "forbidden_commands": ["rm -rf /"],
                    "allowed_file_paths": ["*"],
                    "forbidden_file_paths": [],
                },
                "limits": {
                    "max_execution_timeout_seconds": 60,
                    "max_iterations": 10,
                    "max_file_modifications": 5,
                },
            },
            "file_operations": file_ops or [],
            "verification": {
                "automated_checks": checks or [],
                "acceptance_statement": "Mock verification complete",
            },
        }

    def test_mock_executor_file_create_and_modify(self):
        file_ops = [
            {
                "path": "src/app.py",
                "operation": "create",
                "content": "def run():\n    return 0\n",
                "is_critical": True,
            },
            {
                "path": "src/app.py",
                "operation": "modify",
                "content": "def run():\n    return 42\n",
                "is_critical": True,
            },
        ]
        checks = [
            {
                "id": "c1",
                "name": "Check src/app.py exists",
                "type": "file_exists",
                "target_path": "src/app.py",
                "is_blocking": True,
            },
            {
                "id": "c2",
                "name": "Check return 42 in app.py",
                "type": "file_contains",
                "target_path": "src/app.py",
                "expected_content": "return 42",
                "is_blocking": True,
            },
            {
                "id": "c3",
                "name": "Check regex match",
                "type": "file_matches_regex",
                "target_path": "src/app.py",
                "pattern": r"def\s+run\(\):",
                "is_blocking": True,
            },
            {
                "id": "c4",
                "name": "Run python command verification",
                "type": "command",
                "command": f'{sys.executable} -c "print(\'hello_verification\')"',
                "expected_exit_code": 0,
                "expected_stdout_contains": ["hello_verification"],
                "is_blocking": True,
            },
        ]

        payload = self._build_payload("task-success-01", file_ops=file_ops, checks=checks)
        self.store.create_task(payload)

        events = []
        fb = self.executor.execute(payload, self.store, event_broadcaster=events.append)

        # Check final state
        self.assertEqual(fb["state"], TaskState.SUCCEEDED)
        task_rec = self.store.get_task("task-success-01")
        self.assertEqual(task_rec.state, TaskState.SUCCEEDED)

        # Verify files on disk
        app_file = self.workspace_root / "src" / "app.py"
        self.assertTrue(app_file.exists())
        self.assertIn("return 42", app_file.read_text())

        # Verify artifacts
        artifacts = self.store.get_artifacts("task-success-01")
        self.assertEqual(len(artifacts), 2)
        self.assertEqual(artifacts[0]["path"], "src/app.py")

        # Verify check results
        v_results = self.store.get_verification_results("task-success-01")
        self.assertEqual(len(v_results), 4)
        for vr in v_results:
            self.assertEqual(vr["status"], "passed")

    def test_mock_executor_file_delete_and_rename(self):
        orig_file = self.workspace_root / "old_file.txt"
        orig_file.write_text("old content")

        delete_file = self.workspace_root / "to_delete.txt"
        delete_file.write_text("delete me")

        file_ops = [
            {
                "path": "old_file.txt",
                "operation": "rename",
                "target_path": "new_file.txt",
                "is_critical": True,
            },
            {
                "path": "to_delete.txt",
                "operation": "delete",
                "is_critical": True,
            },
        ]
        payload = self._build_payload("task-rename-del", file_ops=file_ops)
        self.store.create_task(payload)

        fb = self.executor.execute(payload, self.store)
        self.assertEqual(fb["state"], TaskState.SUCCEEDED)

        self.assertFalse(delete_file.exists())
        self.assertFalse(orig_file.exists())
        self.assertTrue((self.workspace_root / "new_file.txt").exists())

    def test_mock_executor_inspect_operation(self):
        f = self.workspace_root / "inspect_me.txt"
        f.write_text("inspectable content")
        file_ops = [{"path": "inspect_me.txt", "operation": "inspect", "is_critical": False}]
        payload = self._build_payload("task-inspect", file_ops=file_ops)
        self.store.create_task(payload)

        fb = self.executor.execute(payload, self.store)
        self.assertEqual(fb["state"], TaskState.SUCCEEDED)

    def test_mock_executor_path_traversal_jail(self):
        file_ops = [
            {
                "path": "../../outside_jail.txt",
                "operation": "create",
                "is_critical": True,
                "content": "jail escape",
            }
        ]
        payload = self._build_payload("task-jail-escape", file_ops=file_ops)
        self.store.create_task(payload)

        fb = self.executor.execute(payload, self.store)
        self.assertEqual(fb["state"], TaskState.FAILED)
        self.assertEqual(fb["error"]["code"], "ERR_FORBIDDEN_PATH")

    def test_mock_executor_verification_failure_blocking(self):
        checks = [
            {
                "id": "c-fail",
                "name": "Check missing file",
                "type": "file_exists",
                "target_path": "nonexistent.txt",
                "is_blocking": True,
            }
        ]
        payload = self._build_payload("task-ver-fail", checks=checks)
        self.store.create_task(payload)

        fb = self.executor.execute(payload, self.store)
        self.assertEqual(fb["state"], TaskState.FAILED)
        self.assertEqual(fb["error"]["code"], "ERR_VERIFICATION_FAILED")

    def test_mock_executor_non_blocking_check_failure(self):
        checks = [
            {
                "id": "c-warn",
                "name": "Non blocking missing file",
                "type": "file_exists",
                "target_path": "optional_missing.txt",
                "is_blocking": False,
            }
        ]
        payload = self._build_payload("task-ver-warn", checks=checks)
        self.store.create_task(payload)

        fb = self.executor.execute(payload, self.store)
        self.assertEqual(fb["state"], TaskState.SUCCEEDED)

    def test_mock_executor_file_contains_and_regex_failure(self):
        f = self.workspace_root / "test_text.txt"
        f.write_text("alpha beta gamma")

        # Missing content
        checks1 = [
            {
                "id": "c-miss",
                "name": "Missing content",
                "type": "file_contains",
                "target_path": "test_text.txt",
                "pattern": "delta",
                "is_blocking": True,
            }
        ]
        p1 = self._build_payload("task-chk-miss", checks=checks1)
        self.store.create_task(p1)
        fb1 = self.executor.execute(p1, self.store)
        self.assertEqual(fb1["state"], TaskState.FAILED)

        # Regex mismatch
        checks2 = [
            {
                "id": "c-rgx",
                "name": "Regex mismatch",
                "type": "file_matches_regex",
                "target_path": "test_text.txt",
                "pattern": r"^[0-9]+$",
                "is_blocking": True,
            }
        ]
        p2 = self._build_payload("task-chk-rgx", checks=checks2)
        self.store.create_task(p2)
        fb2 = self.executor.execute(p2, self.store)
        self.assertEqual(fb2["state"], TaskState.FAILED)

    def test_mock_executor_dry_run_mode(self):
        file_ops = [
            {
                "path": "dry_run_target.txt",
                "operation": "create",
                "content": "should not be written",
                "is_critical": True,
            }
        ]
        payload = self._build_payload("task-dry-run", file_ops=file_ops, mode="dry_run")
        self.store.create_task(payload)

        fb = self.executor.execute(payload, self.store)
        self.assertEqual(fb["state"], TaskState.SUCCEEDED)
        target_p = self.workspace_root / "dry_run_target.txt"
        self.assertFalse(target_p.exists())

    def test_mock_executor_interactive_approval_gate(self):
        payload = self._build_payload("task-approval-needed", mode="interactive_approval")
        self.store.create_task(payload)

        fb = self.executor.execute(payload, self.store)
    def test_mock_executor_command_exit_code_failure(self):
        checks = [
            {
                "id": "c-fail-exit",
                "name": "Command exit failure",
                "type": "command",
                "command": f'{sys.executable} -c "import sys; sys.exit(2)"',
                "expected_exit_code": 0,
                "is_blocking": True,
            }
        ]
        payload = self._build_payload("task-cmd-fail", checks=checks)
        self.store.create_task(payload)
        fb = self.executor.execute(payload, self.store)
        self.assertEqual(fb["state"], TaskState.FAILED)

    def test_mock_executor_cancel_and_is_running(self):
        self.assertFalse(self.executor.is_running("t-none"))
        self.assertFalse(self.executor.cancel("t-none"))

    def test_mock_executor_test_suite_and_stdout_check_failures(self):
        # Missing expected stdout
        checks1 = [
            {
                "id": "c-chk-stdout",
                "name": "Stdout check",
                "type": "command",
                "command": f'{sys.executable} -c "print(\'hello world\')"',
                "expected_stdout_contains": ["goodbye"],
                "is_blocking": True,
            }
        ]
        p1 = self._build_payload("task-chk-std", checks=checks1)
        self.store.create_task(p1)
        fb1 = self.executor.execute(p1, self.store)
        self.assertEqual(fb1["state"], TaskState.FAILED)

        # Forbidden stderr contains failure
        checks2 = [
            {
                "id": "c-chk-stderr",
                "name": "Stderr check",
                "type": "test_suite",
                "command": f'{sys.executable} -c "import sys; sys.stderr.write(\'fatal error occurred\\n\')"',
                "forbidden_stderr_contains": ["fatal error"],
                "is_blocking": True,
            }
        ]
        p2 = self._build_payload("task-chk-err", checks=checks2)
        self.store.create_task(p2)
        fb2 = self.executor.execute(p2, self.store)
        self.assertEqual(fb2["state"], TaskState.FAILED)

    def test_mock_executor_checks_on_nonexistent_files_and_timeout(self):
        # File contains on nonexistent file
        c1 = [{"id": "c-none1", "name": "Contains on missing", "type": "file_contains", "target_path": "nonexistent.txt", "pattern": "abc", "is_blocking": True}]
        p1 = self._build_payload("task-c-none1", checks=c1)
        self.store.create_task(p1)
        self.assertEqual(self.executor.execute(p1, self.store)["state"], TaskState.FAILED)

        # File regex on nonexistent file
        c2 = [{"id": "c-none2", "name": "Regex on missing", "type": "file_matches_regex", "target_path": "nonexistent.txt", "pattern": r"^abc$", "is_blocking": True}]
        p2 = self._build_payload("task-c-none2", checks=c2)
        self.store.create_task(p2)
        self.assertEqual(self.executor.execute(p2, self.store)["state"], TaskState.FAILED)

    def test_mock_executor_direct_execute_without_precreate_and_path_jail(self):
        # 1. Execute without pre-created task record in store
        p1 = self._build_payload("task-no-precreate")
        res1 = self.executor.execute(p1, self.store)
        self.assertEqual(res1["state"], TaskState.SUCCEEDED)
        self.assertIsNotNone(self.store.get_task("task-no-precreate"))

        # 2. Path traversal in constraints.environment.cwd
        p2 = self._build_payload("task-traversal-cwd")
        p2["constraints"]["environment"]["cwd"] = "/etc"
        res2 = self.executor.execute(p2, self.store)
        self.assertEqual(res2["state"], TaskState.FAILED)


class TestPIAgentExecutor(unittest.TestCase):
    """Test PIAgentExecutor prompt generation, CLI execution, and error handling."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.store = StateStore(":memory:")

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_pi_executor_prompt_compilation_and_execution(self):
        mock_pi = PIAgentExecutor(
            pi_binary_path=sys.executable,
            allowed_roots=[self.workspace_root],
        )

        payload = {
            "schema_version": "1.0.0",
            "context": {
                "session_id": "c0eebc99-9c0b-4ef8-bb6d-6bb9bd380a99",
                "source": "chatgpt_web_action",
                "user_intent": "Refactor auth logic",
                "decision_rationale": "Extract token helper function",
                "timestamp": "2026-09-01T04:00:00Z",
            },
            "task": {
                "task_id": "task-pi-01",
                "title": "Refactor Auth",
                "description": "Refactor authentication methods in auth.py.",
                "category": "refactor",
                "priority": "medium",
                "complexity": "medium",
            },
            "constraints": {
                "environment": {"cwd": str(self.workspace_root)},
                "security_policy": {"execution_mode": "sandboxed"},
            },
        }
        self.store.create_task(payload)

        # Mock the process runner to simulate a successful PI CLI exit
        with patch.object(
            mock_pi.process_runner,
            "run",
            return_value=ProcessResult(exit_code=0, stdout="Simulated PI output\n", stderr="", duration_seconds=0.1),
        ):
            fb = mock_pi.execute(payload, self.store)
            self.assertEqual(fb["state"], TaskState.SUCCEEDED)

    def test_pi_executor_dry_run_mode(self):
        mock_pi = PIAgentExecutor(
            pi_binary_path=sys.executable,
            allowed_roots=[self.workspace_root],
        )
        payload = {
            "task": {"task_id": "task-pi-dry", "title": "Dry", "description": "desc"},
            "context": {"user_intent": "test", "decision_rationale": "test"},
            "constraints": {"environment": {"cwd": str(self.workspace_root)}, "security_policy": {"execution_mode": "dry_run"}},
        }
        self.store.create_task(payload)
        with patch.object(
            mock_pi.process_runner,
            "run",
            return_value=ProcessResult(exit_code=0, stdout="Dry run output", stderr="", duration_seconds=0.1),
        ):
            fb = mock_pi.execute(payload, self.store)
            self.assertEqual(fb["state"], TaskState.SUCCEEDED)

    def test_pi_executor_missing_binary(self):
        mock_pi = PIAgentExecutor(
            pi_binary_path="/nonexistent/path/to/pi_agent_bin",
            allowed_roots=[self.workspace_root],
        )
        payload = {
            "task": {"task_id": "task-pi-missing", "title": "Missing", "description": "desc"},
            "context": {"user_intent": "test", "decision_rationale": "test"},
            "constraints": {"environment": {"cwd": str(self.workspace_root)}, "security_policy": {"execution_mode": "sandboxed"}},
        }
        self.store.create_task(payload)
        fb = mock_pi.execute(payload, self.store)
        self.assertEqual(fb["state"], TaskState.FAILED)
        self.assertEqual(fb["error"]["code"], "ERR_DEPENDENCY_MISSING")

    def test_pi_executor_cli_failure(self):
        mock_pi = PIAgentExecutor(
            pi_binary_path=sys.executable,
            allowed_roots=[self.workspace_root],
        )
        payload = {
            "task": {"task_id": "task-pi-fail", "title": "Fail", "description": "desc"},
            "context": {"user_intent": "test", "decision_rationale": "test"},
            "constraints": {"environment": {"cwd": str(self.workspace_root)}, "security_policy": {"execution_mode": "sandboxed"}},
        }
        self.store.create_task(payload)

        with patch.object(
            mock_pi.process_runner,
            "run",
            return_value=ProcessResult(exit_code=1, stdout="", stderr="PI agent error", duration_seconds=0.1),
        ):
            fb = mock_pi.execute(payload, self.store)
            self.assertEqual(fb["state"], TaskState.FAILED)

    def test_pi_executor_interactive_approval_and_timeout(self):
        mock_pi = PIAgentExecutor(pi_binary_path=sys.executable, allowed_roots=[self.workspace_root])

        # Interactive approval
        p_app = {
            "task": {"task_id": "task-pi-app", "title": "App", "description": "desc"},
            "context": {"user_intent": "test", "decision_rationale": "test"},
            "constraints": {"environment": {"cwd": str(self.workspace_root)}, "security_policy": {"execution_mode": "interactive_approval"}},
        }
        self.store.create_task(p_app)
        fb_app = mock_pi.execute(p_app, self.store)
        self.assertEqual(fb_app["state"], TaskState.WAITING_CONFIRMATION)

        # Timeout
        p_to = {
            "task": {"task_id": "task-pi-to", "title": "Timeout", "description": "desc"},
            "context": {"user_intent": "test", "decision_rationale": "test"},
            "constraints": {"environment": {"cwd": str(self.workspace_root)}, "security_policy": {}},
        }
        self.store.create_task(p_to)
        with patch.object(
            mock_pi.process_runner,
            "run",
            return_value=ProcessResult(exit_code=0, stdout="", stderr="", duration_seconds=10.0, timed_out=True),
        ):
            fb_to = mock_pi.execute(p_to, self.store)
            self.assertEqual(fb_to["state"], TaskState.FAILED)
            self.assertEqual(fb_to["error"]["code"], "ERR_TIMEOUT")

    def test_pi_executor_with_verifications(self):
        mock_pi = PIAgentExecutor(pi_binary_path=sys.executable, allowed_roots=[self.workspace_root])
        # Success with verification
        (self.workspace_root / "verified.txt").write_text("verified content")
        payload = {
            "task": {"task_id": "task-pi-ver", "title": "Ver", "description": "desc"},
            "context": {"user_intent": "test", "decision_rationale": "test"},
            "constraints": {"environment": {"cwd": str(self.workspace_root)}, "security_policy": {}},
            "verification": {
                "automated_checks": [
                    {
                        "id": "c-file",
                        "name": "File exists",
                        "type": "file_exists",
                        "target_path": "verified.txt",
                        "is_blocking": True,
                    },
                    {
                        "id": "c-cmd",
                        "name": "Command check",
                        "type": "command",
                        "command": "echo ok",
                        "expected_exit_code": 0,
                        "is_blocking": True,
                    },
                ],
                "acceptance_statement": "Done",
            },
        }
        self.store.create_task(payload)
        with patch.object(
            mock_pi.process_runner,
            "run",
            return_value=ProcessResult(exit_code=0, stdout="Simulated output", stderr="", duration_seconds=0.1),
        ):
            fb = mock_pi.execute(payload, self.store)
            self.assertEqual(fb["state"], TaskState.SUCCEEDED)

    def test_pi_executor_cancel_and_is_running(self):
        mock_pi = PIAgentExecutor(pi_binary_path=sys.executable, allowed_roots=[self.workspace_root])
        self.assertFalse(mock_pi.is_running("t-none"))
        self.assertFalse(mock_pi.cancel("t-none"))

    def test_pi_executor_format_pi_prompt(self):
        mock_pi = PIAgentExecutor(pi_binary_path=sys.executable, allowed_roots=[self.workspace_root])
        payload = {
            "task": {"task_id": "t-prompt", "title": "Prompt Task", "description": "Do stuff"},
            "context": {"user_intent": "Intent", "decision_rationale": "Rationale"},
            "file_operations": [{"path": "file.py", "operation": "create", "content": "print(1)", "description": "desc"}],
            "verification": {"acceptance_statement": "Must pass all tests."},
        }
        prompt = mock_pi._format_pi_prompt(payload)
        self.assertIn("Prompt Task", prompt)
        self.assertIn("print(1)", prompt)
        self.assertIn("Must pass all tests.", prompt)

    def test_base_agent_executor_methods(self):
        from bridge.executors import BaseAgentExecutor
        with self.assertRaises(TypeError):
            BaseAgentExecutor()


class TestTaskRunnerPool(unittest.TestCase):
    """Test TaskRunnerPool queue management, concurrency, and task cancellation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.store = StateStore(":memory:")
        self.executor = MockAgentExecutor(allowed_roots=[self.workspace_root])
        self.pool = TaskRunnerPool(self.executor, self.store, max_workers=2)

    def tearDown(self):
        self.pool.shutdown(wait=True)
        self.store.close()
        self.temp_dir.cleanup()

    def test_submit_and_execute_task(self):
        payload = {
            "schema_version": "1.0.0",
            "context": {
                "session_id": "c0eebc99-9c0b-4ef8-bb6d-6bb9bd380a99",
                "source": "cli",
                "user_intent": "Pool task test",
                "decision_rationale": "Verify async runner pool",
            },
            "task": {
                "task_id": "task-pool-01",
                "title": "Pool Async Task",
                "description": "Execute task in background pool.",
                "category": "feature",
                "priority": "medium",
                "complexity": "small",
            },
            "constraints": {
                "environment": {"cwd": str(self.workspace_root)},
                "security_policy": {"execution_mode": "sandboxed"},
            },
            "file_operations": [
                {
                    "path": "pool_output.txt",
                    "operation": "create",
                    "content": "generated in pool",
                    "is_critical": True,
                }
            ],
            "verification": {"automated_checks": [], "acceptance_statement": "Done"},
        }

        events = []
        self.pool.add_event_subscriber(events.append)

        rec = self.pool.submit_task(payload)
        self.assertEqual(rec.task_id, "task-pool-01")
        self.assertEqual(rec.state, TaskState.QUEUED)

        start = time.time()
        while time.time() - start < 5.0:
            task_status = self.store.get_task("task-pool-01")
            if task_status and task_status.state in (TaskState.SUCCEEDED, TaskState.FAILED):
                break
            time.sleep(0.05)

        final_rec = self.store.get_task("task-pool-01")
        self.assertEqual(final_rec.state, TaskState.SUCCEEDED)
        self.assertTrue((self.workspace_root / "pool_output.txt").exists())
        self.assertGreaterEqual(len(events), 1)

    def test_cancel_task(self):
        payload = {
            "task": {"task_id": "task-cancel-01", "title": "To Cancel", "description": "desc"},
            "context": {"user_intent": "test", "decision_rationale": "test"},
            "constraints": {"environment": {"cwd": str(self.workspace_root)}, "security_policy": {"execution_mode": "interactive_approval"}},
        }
        self.pool.submit_task(payload)
        time.sleep(0.1)
        cancelled = self.pool.cancel_task("task-cancel-01")
        self.assertTrue(cancelled)

        rec = self.store.get_task("task-cancel-01")
        self.assertEqual(rec.state, TaskState.CANCELLED)

    def test_pool_queries_and_subscribers(self):
        # Subscriber error handling
        def broken_subscriber(evt):
            raise RuntimeError("subscriber error")

        self.pool.add_event_subscriber(broken_subscriber)
        self.pool._broadcast({"event": "test"})


if __name__ == "__main__":
    unittest.main()
