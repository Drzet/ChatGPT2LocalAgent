"""Integration and REST API tests for BridgeDaemon and HTTP endpoints.

Covers:
- GET /health and /api/health probe
- Constant-time HMAC authentication (401 Unauthorized on missing/invalid token)
- CORS and Private Network Access (PNA) headers (OPTIONS and responses)
- POST /api/tasks: valid submissions (201 Created), interactive approval (202 Accepted)
- Schema validation failures (400 Bad Request)
- Path traversal rejection (403 Forbidden)
- Payload size ceiling (413 Payload Too Large)
- GET /api/tasks and query filters (state, limit, offset)
- GET /api/tasks/{task_id} and full feedback inspection
- GET /api/tasks/{task_id}/logs
- POST /api/tasks/{task_id}/confirm
- POST /api/tasks/{task_id}/cancel
- GET /api/tasks/{task_id}/events Server-Sent Events (SSE) streaming
- ClipboardMonitor start/stop and markdown detection
- BridgeDaemon lifecycle (Mock and PI executors, token files, CLI main entrypoint)
"""

import http.client
import io
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.daemon import (
    BridgeDaemon,
    BridgeHTTPServer,
    BridgeRequestHandler,
    ClipboardMonitor,
    SSEBroadcaster,
    main,
)
from bridge.executors import MockAgentExecutor, TaskRunnerPool
from bridge.state_store import StateStore, TaskState


def get_valid_api_payload(task_id="api-task-01", cwd=".", mode="sandboxed"):
    return {
        "schema_version": "1.0.0",
        "context": {
            "session_id": "c0eebc99-9c0b-4ef8-bb6d-6bb9bd380a99",
            "source": "chatgpt_web_action",
            "user_intent": "Test daemon REST API",
            "decision_rationale": "Verify endpoint routing and SSE stream delivery",
            "timestamp": "2026-09-01T04:00:00Z",
        },
        "task": {
            "task_id": task_id,
            "title": "API Test Task",
            "description": "Execute task via HTTP POST /api/tasks.",
            "category": "feature",
            "priority": "medium",
            "complexity": "small",
        },
        "constraints": {
            "environment": {"os": "mac", "runtime": "python>=3.11", "cwd": str(cwd)},
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
        "file_operations": [
            {
                "path": "api_result.txt",
                "operation": "create",
                "content": "Created via REST API",
                "is_critical": True,
            }
        ],
        "verification": {
            "automated_checks": [],
            "acceptance_statement": "API Task verification completed",
        },
    }


class DaemonTestClient:
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
            "User-Agent": "TestClient/1.0",
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


class TestDaemonAPI(unittest.TestCase):
    """Integration test suite for BridgeDaemon REST endpoints and SSE streaming."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.auth_token = "test-secret-token-12345"

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
        self.client = DaemonTestClient(self.server, auth_token=self.auth_token)

    def tearDown(self):
        self.server.server_close()
        self.pool.shutdown(wait=True)
        self.store.close()
        self.temp_dir.cleanup()

    def test_health_check_no_auth_required(self):
        status, data, headers, _ = self.client.request("GET", "/health", auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["version"], "1.0.0")
        self.assertIn("uptime_seconds", data)

    def test_options_cors_and_pna_preflight(self):
        status, _, headers, _ = self.client.request(
            "OPTIONS",
            "/api/tasks",
            headers={"Access-Control-Request-Private-Network": "true"},
            auth=False,
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Private-Network"), "true")
        self.assertIn("POST", headers.get("Access-Control-Allow-Methods", ""))

    def test_unauthorized_missing_token(self):
        status, data, _, _ = self.client.request("GET", "/api/tasks", auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(data["error"], "ERR_UNAUTHORIZED")

    def test_unauthorized_invalid_token(self):
        status, data, _, _ = self.client.request(
            "GET",
            "/api/tasks",
            headers={"Authorization": "Bearer wrong-token"},
            auth=False,
        )
        self.assertEqual(status, 401)
        self.assertEqual(data["error"], "ERR_UNAUTHORIZED")

    def test_post_task_success(self):
        payload = get_valid_api_payload("task-api-01", cwd=self.workspace_root)
        status, data, headers, _ = self.client.request("POST", "/api/tasks", body=payload)
        self.assertEqual(status, 201)
        self.assertEqual(data["task_id"], "task-api-01")
        self.assertEqual(data["status"], "queued")
        self.assertEqual(headers.get("Access-Control-Allow-Private-Network"), "true")

        time.sleep(0.3)
        get_status, get_data, _, _ = self.client.request("GET", "/api/tasks/task-api-01")
        self.assertEqual(get_status, 200)
        self.assertEqual(get_data["state"], TaskState.SUCCEEDED)
        self.assertTrue((self.workspace_root / "api_result.txt").exists())

    def test_post_task_interactive_approval_gate(self):
        payload = get_valid_api_payload("task-api-approval", cwd=self.workspace_root, mode="interactive_approval")
        status, data, _, _ = self.client.request("POST", "/api/tasks", body=payload)
        self.assertEqual(status, 202)
        self.assertEqual(data["status"], "waiting_confirmation")
        self.assertIn("confirmation_token", data)
        conf_token = data["confirmation_token"]

        c_status, c_data, _, _ = self.client.request(
            "POST",
            "/api/tasks/task-api-approval/confirm",
            body={"token": conf_token},
        )
        self.assertEqual(c_status, 200)
        self.assertEqual(c_data["status"], "queued")

    def test_post_task_schema_validation_error(self):
        bad_payload = {"invalid": "payload"}
        status, data, _, _ = self.client.request("POST", "/api/tasks", body=bad_payload)
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "ERR_SCHEMA_VALIDATION")
        self.assertIn("details", data)

    def test_post_task_path_traversal_forbidden(self):
        payload = get_valid_api_payload("task-escape", cwd="/etc")
        status, data, _, _ = self.client.request("POST", "/api/tasks", body=payload)
        self.assertEqual(status, 403)
        self.assertEqual(data["error"], "ERR_SECURITY_VIOLATION")

    def test_post_task_payload_too_large(self):
        status, data, _, _ = self.client.request(
            "POST",
            "/api/tasks",
            headers={"Content-Length": str(6 * 1024 * 1024)},
        )
        self.assertEqual(status, 413)
        self.assertEqual(data["error"], "ERR_PAYLOAD_TOO_LARGE")

    def test_get_tasks_list_and_filter(self):
        payload = get_valid_api_payload("task-list-01", cwd=self.workspace_root)
        self.client.request("POST", "/api/tasks", body=payload)

        status, data, _, _ = self.client.request("GET", "/api/tasks?limit=10&state=queued")
        self.assertEqual(status, 200)
        self.assertIn("tasks", data)
        self.assertGreaterEqual(data["count"], 0)

    def test_get_nonexistent_task(self):
        status, data, _, _ = self.client.request("GET", "/api/tasks/nonexistent-task-999")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "ERR_TASK_NOT_FOUND")

    def test_get_task_logs(self):
        payload = get_valid_api_payload("task-log-01", cwd=self.workspace_root)
        p_status, _, _, _ = self.client.request("POST", "/api/tasks", body=payload)
        self.assertEqual(p_status, 201)
        time.sleep(0.2)

        status, data, _, _ = self.client.request("GET", "/api/tasks/task-log-01/logs")
        self.assertEqual(status, 200)
        self.assertEqual(data["task_id"], "task-log-01")
        self.assertIn("logs", data)

    def test_post_cancel_task(self):
        payload = get_valid_api_payload("task-to-cancel", cwd=self.workspace_root)
        self.client.request("POST", "/api/tasks", body=payload)
        status, data, _, _ = self.client.request("POST", "/api/tasks/task-to-cancel/cancel")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "cancelled")

    def test_sse_event_streaming(self):
        task_id = "task-sse-test"
        payload = get_valid_api_payload(task_id, cwd=self.workspace_root)
        self.client.request("POST", "/api/tasks", body=payload)

        status, stream_file, headers, sock = self.client.request(
            "GET",
            f"/api/tasks/{task_id}/events",
            stream=True,
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers.get("Content-Type", ""))

        lines = []
        for _ in range(5):
            line = stream_file.readline().decode("utf-8")
            if line:
                lines.append(line)
            if "data:" in line:
                break
        sock.close()
        self.assertTrue(any("data:" in l for l in lines))

    def test_route_not_found(self):
        status, data, _, _ = self.client.request("GET", "/api/unknown/endpoint")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "ERR_NOT_FOUND")

    def test_post_confirm_nonexistent_task(self):
        status, data, _, _ = self.client.request(
            "POST",
            "/api/tasks/nonexistent-xyz/confirm",
            body={"token": "some-tok"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "ERR_TASK_NOT_FOUND")

    def test_post_cancel_nonexistent_task(self):
        status, data, _, _ = self.client.request(
            "POST",
            "/api/tasks/nonexistent-xyz/cancel",
        )
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "ERR_TASK_NOT_FOUND")

    def test_post_malformed_json_body(self):
        status, data, _, _ = self.client.request(
            "POST",
            "/api/tasks",
            body="{broken-json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "ERR_INVALID_JSON")

    def test_post_confirm_invalid_token(self):
        payload = get_valid_api_payload("task-conf-invalid", cwd=self.workspace_root, mode="interactive_approval")
        self.client.request("POST", "/api/tasks", body=payload)
        # Attempt to confirm with wrong token
        status, data, _, _ = self.client.request(
            "POST",
            "/api/tasks/task-conf-invalid/confirm",
            body={"token": "wrong-token-abc"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "ERR_CONFIRMATION_INVALID")

    def test_post_unknown_endpoint(self):
        status, data, _, _ = self.client.request("POST", "/api/unknown/endpoint", body={})
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "ERR_NOT_FOUND")

    def test_sse_broadcaster_subscribe_and_unsubscribe(self):
        broadcaster = SSEBroadcaster()
        q = broadcaster.subscribe("task-broad-01")
        self.assertIn("task-broad-01", broadcaster._subscribers)
        self.assertIn(q, broadcaster._subscribers["task-broad-01"])

        # Broadcast event
        broadcaster.broadcast({"task_id": "task-broad-01", "state": "running"})
        evt = q.get_nowait()
        self.assertEqual(evt["state"], "running")

        # Unsubscribe
        broadcaster.unsubscribe("task-broad-01", q)
        self.assertNotIn(q, broadcaster._subscribers.get("task-broad-01", []))
    def test_query_param_token_auth(self):
        status, data, _, _ = self.client.request(
            "GET",
            f"/api/tasks?token={self.auth_token}",
            auth=False,
        )
        self.assertEqual(status, 200)

    def test_api_health_route(self):
        status, data, _, _ = self.client.request("GET", "/api/health", auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "healthy")

    def test_post_empty_body(self):
        status, data, _, _ = self.client.request("POST", "/api/tasks", headers={"Content-Length": "0"})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "ERR_BAD_REQUEST")

    def test_post_payload_too_large(self):
        status, data, _, _ = self.client.request(
            "POST",
            "/api/tasks",
            headers={"Content-Length": str(6 * 1024 * 1024)},
        )
        self.assertEqual(status, 413)
        self.assertEqual(data["error"], "ERR_PAYLOAD_TOO_LARGE")

    def test_get_tasks_pagination(self):
        # Create multiple tasks
        for idx in range(3):
            p = get_valid_api_payload(f"task-page-{idx}", cwd=self.workspace_root)
            self.client.request("POST", "/api/tasks", body=p)

        status, data, _, _ = self.client.request("GET", "/api/tasks?limit=2&offset=1")
        self.assertEqual(status, 200)
        self.assertLessEqual(len(data["tasks"]), 2)

    def test_sse_events_nonexistent_task(self):
        status, data, _, _ = self.client.request("GET", "/api/tasks/nonexistent-sse-task/events")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "ERR_TASK_NOT_FOUND")


class TestClipboardAndDaemonLifecycle(unittest.TestCase):
    """Test suite for ClipboardMonitor and BridgeDaemon lifecycle coordinator."""

    def test_clipboard_monitor_macos_and_xclip(self):
        submitted = []
        monitor = ClipboardMonitor(task_submit_callback=submitted.append, poll_interval=0.05)

        # Test pbpaste extraction
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="```json\n" + json.dumps(get_valid_api_payload("clip-01")) + "\n```")
            text = monitor._get_clipboard_text()
            self.assertIn("clip-01", text)

        # Test start and stop
        monitor.start()
        self.assertTrue(monitor._thread.is_alive())
        monitor.stop()
        self.assertFalse(monitor._thread.is_alive())

    def test_bridge_daemon_initialization_options(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            inbox = ws / "inbox"
            inbox.mkdir()

            # Mock executor daemon
            daemon_mock = BridgeDaemon(
                host="127.0.0.1",
                port=0,
                auth_token="daemon-token",
                allowed_roots=[ws],
                executor_type="mock",
                db_path=":memory:",
                inbox_dir=inbox,
                enable_clipboard=False,
            )
            self.assertEqual(daemon_mock.auth_token, "daemon-token")
            self.assertIsNotNone(daemon_mock.inbox_watcher)
            daemon_mock.http_server.server_close()
            daemon_mock.state_store.close()
            daemon_mock.runner_pool.shutdown(wait=False)

            # PI executor daemon
            daemon_pi = BridgeDaemon(
                host="127.0.0.1",
                port=0,
                auth_token=None,
                allowed_roots=[ws],
                executor_type="pi",
                pi_binary_path="/usr/bin/python3",
                db_path=":memory:",
                enable_clipboard=False,
            )
            self.assertIsNotNone(daemon_pi.auth_token)
            self.assertEqual(daemon_pi.executor.pi_binary, "/usr/bin/python3")
            daemon_pi.http_server.server_close()
            daemon_pi.state_store.close()
            daemon_pi.runner_pool.shutdown(wait=False)

    def test_cli_main_entrypoint(self):
        test_args = [
            "bridge_daemon",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--token",
            "cli-token-123",
            "--no-inbox",
        ]
        with patch.object(sys, "argv", test_args):
            with patch("bridge.daemon.BridgeDaemon.start") as mock_start:
                main()
                mock_start.assert_called_once_with(block=True)

    def test_bridge_daemon_start_non_blocking_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir)
            daemon = BridgeDaemon(
                host="127.0.0.1",
                port=0,
                auth_token="d-tok",
                allowed_roots=[ws],
                executor_type="mock",
                db_path=":memory:",
                enable_clipboard=False,
            )
            with patch.object(daemon.http_server, "serve_forever"), patch.object(daemon.http_server, "shutdown"):
                daemon.start(block=False)
                self.assertIsNotNone(daemon._server_thread)
                daemon.stop()


if __name__ == "__main__":
    unittest.main()
