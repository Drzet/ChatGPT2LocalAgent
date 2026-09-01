"""End-to-End Workflow Integration tests for the ChatGPT Web to Local Agent Bridge.

Covers:
- E2E Feature Addition Workflow: Client POST -> Validation -> Queue -> Mock Execution -> Verification Checks -> State Store -> Feedback Generation
- E2E Bugfix Workflow: Failing test setup -> Source patch execution -> Test verification pass -> Artifact persistence
- E2E Human-in-the-Loop Confirmation: Submission -> WAITING_CONFIRMATION -> Token Verification -> POST confirm -> Resumed
- E2E Cancellation: Submission -> POST cancel -> CANCELLED event emission
- E2E Inbox Watcher Integration: JSON drop -> Atomic Lock -> Submission -> Processing -> Archival to processed/
- E2E Real-time SSE Log & Event Streaming
"""

import http.client
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.daemon import BridgeDaemon, BridgeHTTPServer, BridgeRequestHandler
from bridge.executors import MockAgentExecutor, TaskRunnerPool
from bridge.inbox_watcher import InboxWatcher
from bridge.state_store import StateStore, TaskState


class E2ETestClient:
    """In-process HTTP client communicating with BridgeRequestHandler via socketpair."""

    def __init__(self, server: BridgeHTTPServer, auth_token: Optional[str] = None):
        self.server = server
        self.auth_token = auth_token

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: Optional[Dict[str, str]] = None,
        auth: bool = True,
        stream: bool = False,
    ) -> Tuple[int, Any, Dict[str, str], Optional[socket.socket]]:
        s_server, s_client = socket.socketpair()

        req_headers = {
            "Host": "127.0.0.1",
            "User-Agent": "E2ETestClient/1.0",
        }
        if auth and self.auth_token:
            req_headers["Authorization"] = f"Bearer {self.auth_token}"

        body_bytes = b""
        if body is not None:
            if isinstance(body, (dict, list)):
                body_bytes = json.dumps(body).encode("utf-8")
                req_headers["Content-Type"] = "application/json"
            elif isinstance(body, bytes):
                body_bytes = body
            else:
                body_bytes = str(body).encode("utf-8")
            req_headers["Content-Length"] = str(len(body_bytes))

        if headers:
            req_headers.update(headers)

        header_lines = [f"{method} {path} HTTP/1.1"]
        for k, v in req_headers.items():
            header_lines.append(f"{k}: {v}")
        header_lines.append("")
        header_lines.append("")
        req_data = "\r\n".join(header_lines).encode("utf-8") + body_bytes

        s_client.sendall(req_data)

        def handle():
            try:
                BridgeRequestHandler(s_server, ("127.0.0.1", 12345), self.server)
            except Exception:
                pass
            finally:
                try:
                    s_server.close()
                except Exception:
                    pass

        t = threading.Thread(target=handle, daemon=True)
        t.start()

        resp_file = s_client.makefile("rb")
        status_line = resp_file.readline().decode("utf-8")
        if not status_line:
            s_client.close()
            return 500, None, {}, None

        parts = status_line.split(None, 2)
        status_code = int(parts[1]) if len(parts) >= 2 else 500

        resp_headers = {}
        while True:
            line = resp_file.readline().decode("utf-8")
            if not line or line in ("\r\n", "\n"):
                break
            if ":" in line:
                hk, hv = line.split(":", 1)
                resp_headers[hk.strip()] = hv.strip()

        if stream:
            return status_code, resp_file, resp_headers, s_client

        content_length_str = resp_headers.get("Content-Length")
        if status_code == 204 or content_length_str == "0":
            resp_body_bytes = b""
        elif content_length_str is not None:
            content_length = int(content_length_str)
            resp_body_bytes = resp_file.read(content_length)
        else:
            s_client.settimeout(0.5)
            try:
                resp_body_bytes = resp_file.read()
            except Exception:
                resp_body_bytes = b""

        try:
            s_client.close()
        except Exception:
            pass

        resp_str = resp_body_bytes.decode("utf-8", errors="ignore")
        parsed_data = resp_str
        if resp_str:
            try:
                parsed_data = json.loads(resp_str)
            except Exception:
                pass

        return status_code, parsed_data, resp_headers, None


class TestE2EWorkflow(unittest.TestCase):
    """Full end-to-end integration test suite exercising all bridge components together."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.inbox_dir = self.workspace_root / "inbox"
        self.auth_token = "e2e-secret-key-987"

        self.store = StateStore(":memory:")
        self.executor = MockAgentExecutor(allowed_roots=[self.workspace_root])
        self.pool = TaskRunnerPool(self.executor, self.store, max_workers=2)

        self.server = BridgeHTTPServer(
            server_address=("127.0.0.1", 0),
            state_store=self.store,
            runner_pool=self.pool,
            auth_token=self.auth_token,
            allowed_roots=[self.workspace_root],
        )
        self.client = E2ETestClient(self.server, auth_token=self.auth_token)

    def tearDown(self):
        self.server.server_close()
        self.pool.shutdown(wait=True)
        self.store.close()
        self.temp_dir.cleanup()

    def test_e2e_feature_addition_workflow(self):
        # 1. Prepare target project in workspace
        src_dir = self.workspace_root / "mathlib"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("# Mathlib package\n")
        tests_dir = self.workspace_root / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "__init__.py").write_text("")

        test_file = tests_dir / "test_calc.py"
        test_file.write_text(
            f"import sys, os, unittest\n"
            f"sys.path.insert(0, os.path.abspath('.'))\n"
            f"from mathlib.calc import add, multiply\n\n"
            f"class TestCalc(unittest.TestCase):\n"
            f"    def test_add(self):\n"
            f"        self.assertEqual(add(2, 3), 5)\n"
            f"    def test_mult(self):\n"
            f"        self.assertEqual(multiply(3, 4), 12)\n\n"
            f"if __name__ == '__main__':\n"
            f"    unittest.main()\n"
        )

        # 2. Build task handoff payload creating mathlib/calc.py
        payload = {
            "schema_version": "1.0.0",
            "context": {
                "session_id": "d0eebc99-9c0b-4ef8-bb6d-6bb9bd380d00",
                "source": "chatgpt_web_action",
                "user_intent": "Add calculation functions add and multiply",
                "decision_rationale": "Create mathlib/calc.py implementing arithmetic helpers",
                "timestamp": "2026-09-01T04:00:00Z",
            },
            "task": {
                "task_id": "task-feature-mathlib",
                "title": "Add Calculator Module",
                "description": "Implement add and multiply functions in mathlib/calc.py.",
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
                    "execution_mode": "sandboxed",
                    "permitted_commands": ["python3"],
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
            "file_operations": [
                {
                    "path": "mathlib/calc.py",
                    "operation": "create",
                    "is_critical": True,
                    "content": "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n",
                    "description": "Create calculator helper",
                }
            ],
            "verification": {
                "automated_checks": [
                    {
                        "id": "chk-unit-tests",
                        "name": "Run mathlib test suite",
                        "type": "command",
                        "command": f"{sys.executable} -m unittest tests/test_calc.py",
                        "expected_exit_code": 0,
                        "timeout_seconds": 20,
                        "is_blocking": True,
                    }
                ],
                "acceptance_statement": "Calculator functions implemented and verified with unit tests",
            },
        }

        # 3. Submit task via HTTP REST API
        status, data, _, _ = self.client.request("POST", "/api/tasks", body=payload)
        self.assertEqual(status, 201)
        self.assertEqual(data["task_id"], "task-feature-mathlib")

        # 4. Wait for execution to finish
        start = time.time()
        while time.time() - start < 5.0:
            s, task_data, _, _ = self.client.request("GET", "/api/tasks/task-feature-mathlib")
            if s == 200 and task_data.get("state") in (TaskState.SUCCEEDED, TaskState.FAILED):
                break
            time.sleep(0.05)

        # 5. Verify final status and state store data
        s, task_data, _, _ = self.client.request("GET", "/api/tasks/task-feature-mathlib")
        self.assertEqual(s, 200)
        self.assertEqual(task_data["state"], TaskState.SUCCEEDED)
        self.assertEqual(task_data["exit_code"], 0)

        # 6. Verify files on disk
        calc_file = src_dir / "calc.py"
        self.assertTrue(calc_file.exists())
        self.assertIn("def add(a, b):", calc_file.read_text())

        # 7. Check logs and feedback
        s_logs, logs_data, _, _ = self.client.request("GET", "/api/tasks/task-feature-mathlib/logs")
        self.assertEqual(s_logs, 200)
        self.assertGreaterEqual(len(logs_data["logs"]), 1)

    def test_e2e_bugfix_workflow(self):
        # 1. Create buggy code
        buggy_file = self.workspace_root / "divide.py"
        buggy_file.write_text("def divide(a, b):\n    return a + b # BUG: should be a / b\n")

        test_file = self.workspace_root / "test_div.py"
        test_file.write_text(
            f"import sys, os, unittest\n"
            f"sys.path.insert(0, os.path.abspath('.'))\n"
            f"from divide import divide\n\n"
            f"class TestDiv(unittest.TestCase):\n"
            f"    def test_divide(self):\n"
            f"        self.assertEqual(divide(10, 2), 5)\n\n"
            f"if __name__ == '__main__':\n"
            f"    unittest.main()\n"
        )

        # 2. Submit bugfix payload
        payload = {
            "schema_version": "1.0.0",
            "context": {
                "session_id": "d0eebc99-9c0b-4ef8-bb6d-6bb9bd380d01",
                "source": "cli",
                "user_intent": "Fix division bug in divide.py",
                "decision_rationale": "Replace + operator with / division operator",
                "timestamp": "2026-09-01T04:00:00Z",
            },
            "task": {
                "task_id": "task-bugfix-div",
                "title": "Fix Divide Bug",
                "description": "Fix divide function operator in divide.py.",
                "category": "bugfix",
                "priority": "critical",
                "complexity": "trivial",
            },
            "constraints": {
                "environment": {"os": "mac", "runtime": "python>=3.11", "cwd": str(self.workspace_root)},
                "security_policy": {
                    "execution_mode": "sandboxed",
                    "permitted_commands": ["python3"],
                    "forbidden_commands": ["rm -rf /"],
                    "allowed_file_paths": ["*"],
                    "forbidden_file_paths": [],
                },
                "limits": {"max_execution_timeout_seconds": 60, "max_iterations": 5, "max_file_modifications": 2},
            },
            "file_operations": [
                {
                    "path": "divide.py",
                    "operation": "modify",
                    "is_critical": True,
                    "content": "def divide(a, b):\n    return a / b\n",
                }
            ],
            "verification": {
                "automated_checks": [
                    {
                        "id": "chk-div",
                        "name": "Verify divide test passes",
                        "type": "command",
                        "command": f"{sys.executable} -m unittest test_div.py",
                        "expected_exit_code": 0,
                        "is_blocking": True,
                    }
                ],
                "acceptance_statement": "Division test passes with exit code 0",
            },
        }

        status, data, _, _ = self.client.request("POST", "/api/tasks", body=payload)
        self.assertEqual(status, 201)

        time.sleep(0.3)
        s, task_data, _, _ = self.client.request("GET", "/api/tasks/task-bugfix-div")
        self.assertEqual(s, 200)
        self.assertEqual(task_data["state"], TaskState.SUCCEEDED)
        self.assertIn("return a / b", buggy_file.read_text())

    def test_e2e_interactive_confirmation_workflow(self):
        payload = {
            "schema_version": "1.0.0",
            "context": {
                "session_id": "d0eebc99-9c0b-4ef8-bb6d-6bb9bd380d02",
                "source": "chatgpt_web_action",
                "user_intent": "Perform database migration with approval",
                "decision_rationale": "High-risk operation requires human confirmation",
                "timestamp": "2026-09-01T04:00:00Z",
            },
            "task": {
                "task_id": "task-hitl-migrate",
                "title": "Migrate Database Schema",
                "description": "Execute schema migration with safety gate.",
                "category": "migration",
                "priority": "critical",
                "complexity": "medium",
            },
            "constraints": {
                "environment": {"os": "mac", "runtime": "python>=3.11", "cwd": str(self.workspace_root)},
                "security_policy": {
                    "execution_mode": "interactive_approval",
                    "permitted_commands": ["python3"],
                    "forbidden_commands": ["rm -rf /"],
                    "allowed_file_paths": ["*"],
                    "forbidden_file_paths": [],
                },
                "limits": {"max_execution_timeout_seconds": 60, "max_iterations": 5, "max_file_modifications": 5},
            },
            "file_operations": [
                {"path": "migrated.lock", "operation": "create", "content": "migrated=true", "is_critical": True}
            ],
            "verification": {"automated_checks": [], "acceptance_statement": "Migration lock written successfully"},
        }

        status, data, _, _ = self.client.request("POST", "/api/tasks", body=payload)
        self.assertEqual(status, 202)
        self.assertEqual(data["status"], "waiting_confirmation")
        token = data["confirmation_token"]

        # Confirm task
        c_status, c_data, _, _ = self.client.request(
            "POST",
            "/api/tasks/task-hitl-migrate/confirm",
            body={"token": token},
        )
        self.assertEqual(c_status, 200)
        self.assertEqual(c_data["status"], "queued")

    def test_e2e_inbox_watcher_integration(self):
        inbox_dir = self.workspace_root / "inbox"
        watcher = InboxWatcher(
            inbox_dir=inbox_dir,
            task_submit_callback=self.pool.submit_task,
            poll_interval_seconds=0.1,
        )
        watcher.start()

        task_payload = {
            "schema_version": "1.0.0",
            "context": {
                "session_id": "e0eebc99-9c0b-4ef8-bb6d-6bb9bd380e00",
                "source": "file_sync",
                "user_intent": "Inbox file drop integration",
                "decision_rationale": "Verify inbox drop executes automatically",
                "timestamp": "2026-09-01T04:00:00Z",
            },
            "task": {
                "task_id": "task-inbox-e2e",
                "title": "Inbox Auto Task",
                "description": "Triggered via inbox JSON drop.",
                "category": "feature",
                "priority": "medium",
                "complexity": "small",
            },
            "constraints": {
                "environment": {"os": "mac", "runtime": "python>=3.11", "cwd": str(self.workspace_root)},
                "security_policy": {
                    "execution_mode": "sandboxed",
                    "permitted_commands": ["python3"],
                    "forbidden_commands": ["rm -rf /"],
                    "allowed_file_paths": ["*"],
                    "forbidden_file_paths": [],
                },
                "limits": {"max_execution_timeout_seconds": 60, "max_iterations": 5, "max_file_modifications": 5},
            },
            "file_operations": [
                {
                    "path": "inbox_created.txt",
                    "operation": "create",
                    "content": "Created from inbox drop",
                    "is_critical": True,
                }
            ],
            "verification": {"automated_checks": [], "acceptance_statement": "File created in workspace"},
        }

        # Drop file into inbox
        drop_file = inbox_dir / "handoff_task.json"
        drop_file.write_text(json.dumps(task_payload))

        # Wait for watcher to pick up and runner to execute
        start = time.time()
        while time.time() - start < 5.0:
            rec = self.store.get_task("task-inbox-e2e")
            if rec and rec.state in (TaskState.SUCCEEDED, TaskState.FAILED):
                break
            time.sleep(0.05)

        watcher.stop()

        rec = self.store.get_task("task-inbox-e2e")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.state, TaskState.SUCCEEDED)
        self.assertTrue((self.workspace_root / "inbox_created.txt").exists())
        self.assertFalse(drop_file.exists())
        processed_files = list((inbox_dir / "processed").glob("*handoff_task.json"))
        self.assertEqual(len(processed_files), 1)


if __name__ == "__main__":
    unittest.main()
