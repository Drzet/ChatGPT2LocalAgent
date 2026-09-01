"""HTTP REST API and Server-Sent Events (SSE) Bridge Daemon.

Provides:
- REST Endpoints: /api/tasks, /api/tasks/{id}, /api/tasks/{id}/confirm, /api/tasks/{id}/cancel, /health
- Real-time Server-Sent Events (SSE) streaming at /api/tasks/{id}/events
- Private Network Access (PNA) and CORS support for ChatGPT Web
- Constant-time HMAC authentication
- Optional inbox file watcher and clipboard monitor integration
"""

from __future__ import annotations

import argparse
import datetime
import http.server
import json
import logging
import os
import queue
import re
import signal
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.executors import (
    BaseAgentExecutor,
    MockAgentExecutor,
    PIAgentExecutor,
    TaskRunnerPool,
)
from bridge.inbox_watcher import InboxWatcher, extract_json_from_markdown
from bridge.schema_validator import SchemaValidationError, validate_task_handoff
from bridge.security import (
    AuthenticationError,
    CommandPolicy,
    PathTraversalError,
    extract_bearer_token,
    generate_secure_token,
    validate_path,
    verify_token,
)
from bridge.state_store import StateStore, TaskRecord, TaskState, utc_now_iso

logger = logging.getLogger("bridge.daemon")


class SSEBroadcaster:
    """Manages active SSE client streams per task."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[queue.Queue]] = {}
        self._lock = threading.Lock()

    def subscribe(self, task_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            if task_id not in self._subscribers:
                self._subscribers[task_id] = []
            self._subscribers[task_id].append(q)
        return q

    def unsubscribe(self, task_id: str, q: queue.Queue) -> None:
        with self._lock:
            if task_id in self._subscribers:
                if q in self._subscribers[task_id]:
                    self._subscribers[task_id].remove(q)
                if not self._subscribers[task_id]:
                    del self._subscribers[task_id]

    def broadcast(self, event: Dict[str, Any]) -> None:
        task_id = event.get("task_id", "")
        with self._lock:
            queues = list(self._subscribers.get(task_id, []))

        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


class BridgeRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request dispatcher for Bridge Daemon."""

    server: BridgeHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)

    def _send_cors_and_pna_headers(self) -> None:
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-API-Key, Accept, Access-Control-Request-Private-Network",
        )
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "86400")

    def do_OPTIONS(self) -> None:
        """Handle CORS and Private Network Access preflights."""
        self.send_response(204)
        self._send_cors_and_pna_headers()
        self.end_headers()

    def _send_json(self, status_code: int, data: Any) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_and_pna_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status_code: int, error_code: str, message: str, details: Any = None) -> None:
        resp = {"error": error_code, "message": message}
        if details is not None:
            resp["details"] = details
        self._send_json(status_code, resp)

    def _authenticate(self) -> bool:
        """Check authentication token against expected token."""
        if not self.server.auth_token:
            return True

        # Check header
        auth_header = self.headers.get("Authorization")
        api_key = self.headers.get("X-API-Key")
        token = extract_bearer_token(auth_header, api_key)

        # Check URL query param (for SSE EventSource)
        if not token:
            parsed_url = urllib.parse.urlparse(self.path)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            if "token" in query_params:
                token = query_params["token"][0]

        if not verify_token(token, self.server.auth_token):
            self._send_error(401, "ERR_UNAUTHORIZED", "Invalid or missing Bearer authentication token")
            return False
        return True

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self._send_error(400, "ERR_BAD_REQUEST", "Empty request body")
                return None
            if content_length > 5 * 1024 * 1024:
                self._send_error(413, "ERR_PAYLOAD_TOO_LARGE", "Payload exceeds 5MB limit")
                return None
            raw = self.rfile.read(content_length)
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as jde:
            self._send_error(400, "ERR_INVALID_JSON", f"Malformed JSON: {jde}")
            return None
        except Exception as exc:
            self._send_error(400, "ERR_BAD_REQUEST", f"Failed to read body: {exc}")
            return None

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query = urllib.parse.parse_qs(parsed_url.query)

        # Health endpoint (no auth required)
        if path in ("/health", "/api/health"):
            uptime = time.time() - self.server.start_time
            self._send_json(
                200,
                {
                    "status": "healthy",
                    "version": "1.0.0",
                    "uptime_seconds": int(uptime),
                    "active_tasks": len(self.server.runner_pool._futures),
                },
            )
            return

        if not self._authenticate():
            return

        # 1. GET /api/tasks
        if path == "/api/tasks":
            state_filter = query.get("state", query.get("status", [None]))[0]
            limit = int(query.get("limit", [50])[0])
            offset = int(query.get("offset", [0])[0])
            tasks = self.server.state_store.list_tasks(state=state_filter, limit=limit, offset=offset)
            self._send_json(200, {"tasks": [t.to_dict() for t in tasks], "count": len(tasks)})
            return

        # 2. GET /api/tasks/{task_id}/events (SSE)
        sse_match = re.match(r"^/api/tasks/([a-zA-Z0-9_-]+)/events$", path)
        if sse_match:
            task_id = sse_match.group(1)
            self._handle_sse(task_id)
            return

        # 3. GET /api/tasks/{task_id}/logs
        logs_match = re.match(r"^/api/tasks/([a-zA-Z0-9_-]+)/logs$", path)
        if logs_match:
            task_id = logs_match.group(1)
            task = self.server.state_store.get_task(task_id)
            if not task:
                self._send_error(404, "ERR_TASK_NOT_FOUND", f"Task '{task_id}' not found")
                return
            logs = self.server.state_store.get_logs(task_id)
            self._send_json(200, {"task_id": task_id, "logs": [l.to_dict() for l in logs]})
            return

        # 4. GET /api/tasks/{task_id}
        task_match = re.match(r"^/api/tasks/([a-zA-Z0-9_-]+)$", path)
        if task_match:
            task_id = task_match.group(1)
            task = self.server.state_store.get_task(task_id)
            if not task:
                self._send_error(404, "ERR_TASK_NOT_FOUND", f"Task '{task_id}' not found")
                return
            feedback = self.server.state_store.build_feedback_event(task_id)
            task_dict = task.to_dict()
            task_dict["feedback"] = feedback
            self._send_json(200, task_dict)
            return

        self._send_error(404, "ERR_NOT_FOUND", f"Route '{path}' not found")

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        if not self._authenticate():
            return

        # 1. POST /api/tasks
        if path == "/api/tasks":
            payload = self._read_json_body()
            if payload is None:
                return

            # Validate schema
            is_valid, errors = validate_task_handoff(payload)
            if not is_valid:
                self._send_error(400, "ERR_SCHEMA_VALIDATION", "Task handoff schema validation failed", details=errors)
                return

            # Validate workspace jail
            env_conf = payload.get("constraints", {}).get("environment", {})
            ws_root_str = env_conf.get("cwd", ".")
            try:
                validate_path(ws_root_str, self.server.allowed_roots, allow_create=True)
            except PathTraversalError as pte:
                self._send_error(403, "ERR_SECURITY_VIOLATION", str(pte))
                return

            # Check if confirmation required
            sec_policy = payload.get("constraints", {}).get("security_policy", {})
            is_approval = (sec_policy.get("execution_mode") == "interactive_approval") or payload.get("require_confirmation", False)

            if is_approval:
                record = self.server.state_store.create_task(payload, initial_state=TaskState.WAITING_CONFIRMATION)
                conf_token = generate_secure_token(16)
                self.server.state_store.set_confirmation_token(record.task_id, conf_token)
                self._send_json(
                    202,
                    {
                        "task_id": record.task_id,
                        "status": "waiting_confirmation",
                        "confirmation_token": conf_token,
                        "created_at": record.created_at,
                        "message": "Task queued and waiting for confirmation approval",
                    },
                )
            else:
                record = self.server.runner_pool.submit_task(payload)
                self._send_json(
                    201,
                    {
                        "task_id": record.task_id,
                        "status": "queued",
                        "created_at": record.created_at,
                        "message": "Task accepted and queued for execution",
                    },
                )
            return

        # 2. POST /api/tasks/{task_id}/confirm
        confirm_match = re.match(r"^/api/tasks/([a-zA-Z0-9_-]+)/confirm$", path)
        if confirm_match:
            task_id = confirm_match.group(1)
            task = self.server.state_store.get_task(task_id)
            if not task:
                self._send_error(404, "ERR_TASK_NOT_FOUND", f"Task '{task_id}' not found")
                return

            token = None
            if self.headers.get("Content-Length"):
                body = self._read_json_body()
                if body:
                    token = body.get("token")

            if not self.server.state_store.verify_and_consume_confirmation(task_id, token):
                self._send_error(400, "ERR_CONFIRMATION_INVALID", "Task is not waiting confirmation or token is invalid")
                return

            # Re-enqueue task
            self.server.runner_pool.submit_task(task.payload)
            self._send_json(200, {"task_id": task_id, "status": "queued", "message": "Task confirmed and resumed"})
            return

        # 3. POST /api/tasks/{task_id}/cancel
        cancel_match = re.match(r"^/api/tasks/([a-zA-Z0-9_-]+)/cancel$", path)
        if cancel_match:
            task_id = cancel_match.group(1)
            task = self.server.state_store.get_task(task_id)
            if not task:
                self._send_error(404, "ERR_TASK_NOT_FOUND", f"Task '{task_id}' not found")
                return

            cancelled = self.server.runner_pool.cancel_task(task_id)
            self._send_json(200, {"task_id": task_id, "status": "cancelled", "message": "Task cancelled successfully"})
            return

        self._send_error(404, "ERR_NOT_FOUND", f"Route '{path}' not found")

    def _handle_sse(self, task_id: str) -> None:
        """Handle Server-Sent Events (SSE) streaming."""
        task = self.server.state_store.get_task(task_id)
        if not task:
            self._send_error(404, "ERR_TASK_NOT_FOUND", f"Task '{task_id}' not found")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._send_cors_and_pna_headers()
        self.end_headers()

        # 1. Replay historical events
        past_events = self.server.state_store.get_events(task_id)
        for evt in past_events:
            data_str = json.dumps(evt)
            msg = f"event: message\ndata: {data_str}\n\n".encode("utf-8")
            try:
                self.wfile.write(msg)
                self.wfile.flush()
            except Exception:
                return

        # 2. Subscribe to live events
        event_queue = self.server.sse_broadcaster.subscribe(task_id)
        try:
            while True:
                try:
                    event = event_queue.get(timeout=1.0)
                    data_str = json.dumps(event)
                    msg = f"event: message\ndata: {data_str}\n\n".encode("utf-8")
                    self.wfile.write(msg)
                    self.wfile.flush()

                    # Exit SSE stream when reaching terminal state
                    state = event.get("state")
                    if state in (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.BLOCKED):
                        break
                except queue.Empty:
                    # Send heartbeat comment
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except Exception:
                        break
        except Exception:
            pass
        finally:
            self.server.sse_broadcaster.unsubscribe(task_id, event_queue)


class BridgeHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded HTTP server holding state store, runner pool, and broadcaster."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        state_store: StateStore,
        runner_pool: TaskRunnerPool,
        auth_token: Optional[str] = None,
        allowed_roots: Optional[List[Union[str, Path]]] = None,
    ):
        super().__init__(server_address, BridgeRequestHandler)
        self.state_store = state_store
        self.runner_pool = runner_pool
        self.auth_token = auth_token
        self.allowed_roots = [Path(r).resolve() for r in allowed_roots] if allowed_roots else [Path.cwd().resolve()]
        self.sse_broadcaster = SSEBroadcaster()
        self.start_time = time.time()

        # Connect SSE broadcaster to task runner events
        self.runner_pool.add_event_subscriber(self.sse_broadcaster.broadcast)


class ClipboardMonitor:
    """Monitors system clipboard on macOS/Linux for structured task payloads."""

    def __init__(self, task_submit_callback: Callable[[Dict[str, Any]], Any], poll_interval: float = 2.0):
        self.task_submit_callback = task_submit_callback
        self.poll_interval = poll_interval
        self._last_hash = ""
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _get_clipboard_text(self) -> str:
        try:
            if sys.platform == "darwin":
                res = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2.0)
                return res.stdout
            elif shutil.which("xclip"):
                res = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=2.0)
                return res.stdout
        except Exception:
            pass
        return ""

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ClipboardMonitor", daemon=True)
        self._thread.start()
        logger.info("Clipboard monitor started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            text = self._get_clipboard_text()
            if text:
                text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
                if text_hash != self._last_hash:
                    self._last_hash = text_hash
                    payload = extract_json_from_markdown(text)
                    if payload and isinstance(payload, dict):
                        is_valid, _ = validate_task_handoff(payload)
                        if is_valid:
                            logger.info("Detected valid task payload on clipboard. Submitting to bridge.")
                            try:
                                self.task_submit_callback(payload)
                            except Exception as e:
                                logger.warning("Failed to submit task from clipboard: %s", e)
            self._stop_event.wait(timeout=self.poll_interval)


class BridgeDaemon:
    """Main lifecycle coordinator for Bridge Daemon, HTTP Server, Inbox Watcher, and Clipboard Monitor."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        auth_token: Optional[str] = None,
        allowed_roots: Optional[List[Union[str, Path]]] = None,
        executor_type: str = "mock",
        pi_binary_path: Optional[str] = None,
        db_path: Union[str, Path] = ":memory:",
        inbox_dir: Optional[Union[str, Path]] = None,
        enable_clipboard: bool = False,
    ):
        self.host = host
        self.port = port
        self.allowed_roots = [Path(r).resolve() for r in allowed_roots] if allowed_roots else [Path.cwd().resolve()]

        # Setup Auth Token
        self.auth_token = auth_token or os.environ.get("BRIDGE_AUTH_TOKEN")
        if not self.auth_token:
            token_file = Path.home() / ".chatgpt_local_bridge" / "token"
            if token_file.exists():
                self.auth_token = token_file.read_text("utf-8").strip()
            else:
                self.auth_token = generate_secure_token(32)
                try:
                    token_file.parent.mkdir(parents=True, exist_ok=True)
                    token_file.write_text(self.auth_token, "utf-8")
                    token_file.chmod(0o600)
                except Exception:
                    pass

        # Setup Persistence
        self.state_store = StateStore(db_path=db_path)

        # Setup Executor
        if executor_type == "pi":
            self.executor: BaseAgentExecutor = PIAgentExecutor(
                pi_binary_path=pi_binary_path,
                allowed_roots=self.allowed_roots,
            )
        else:
            self.executor = MockAgentExecutor(allowed_roots=self.allowed_roots)

        # Setup Runner Pool
        self.runner_pool = TaskRunnerPool(executor=self.executor, state_store=self.state_store, max_workers=2)

        # Setup HTTP Server
        self.http_server = BridgeHTTPServer(
            server_address=(self.host, self.port),
            state_store=self.state_store,
            runner_pool=self.runner_pool,
            auth_token=self.auth_token,
            allowed_roots=self.allowed_roots,
        )

        # Setup Inbox Watcher
        self.inbox_watcher: Optional[InboxWatcher] = None
        if inbox_dir:
            self.inbox_watcher = InboxWatcher(
                inbox_dir=inbox_dir,
                task_submit_callback=self.runner_pool.submit_task,
            )

        # Setup Clipboard Monitor
        self.clipboard_monitor: Optional[ClipboardMonitor] = None
        if enable_clipboard:
            self.clipboard_monitor = ClipboardMonitor(
                task_submit_callback=self.runner_pool.submit_task,
            )

        self._server_thread: Optional[threading.Thread] = None

    def start(self, block: bool = False) -> None:
        """Start daemon HTTP server and background monitors."""
        logger.info("Starting Bridge Daemon on %s:%d (auth_token=%s...)", self.host, self.port, self.auth_token[:8])
        if self.inbox_watcher:
            self.inbox_watcher.start()
        if self.clipboard_monitor:
            self.clipboard_monitor.start()

        if block:
            self.http_server.serve_forever()
        else:
            self._server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
            self._server_thread.start()

    def stop(self) -> None:
        """Stop server and clean up resources."""
        logger.info("Stopping Bridge Daemon...")
        if self.inbox_watcher:
            self.inbox_watcher.stop()
        if self.clipboard_monitor:
            self.clipboard_monitor.stop()

        self.http_server.shutdown()
        self.http_server.server_close()
        self.runner_pool.shutdown(wait=False)
        self.state_store.close()
        logger.info("Bridge Daemon stopped.")


def main() -> None:
    """CLI entrypoint for running Bridge Daemon."""
    parser = argparse.ArgumentParser(description="ChatGPT-to-Local Agent Bridge Daemon")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    parser.add_argument("--token", default=None, help="Secret Bearer authentication token")
    parser.add_argument("--workspace", action="append", help="Authorized workspace root (can specify multiple)")
    parser.add_argument("--executor", choices=["mock", "pi"], default="mock", help="Agent executor type (default: mock)")
    parser.add_argument("--pi-binary", default=None, help="Path to PI CLI executable")
    parser.add_argument("--db", default="tasks.db", help="SQLite database path (default: tasks.db)")
    parser.add_argument("--inbox", default="inbox", help="Inbox directory to monitor (default: inbox)")
    parser.add_argument("--no-inbox", action="store_true", help="Disable inbox file watcher")
    parser.add_argument("--clipboard", action="store_true", help="Enable system clipboard monitor")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    )

    daemon = BridgeDaemon(
        host=args.host,
        port=args.port,
        auth_token=args.token,
        allowed_roots=args.workspace or [os.getcwd()],
        executor_type=args.executor,
        pi_binary_path=args.pi_binary,
        db_path=args.db,
        inbox_dir=None if args.no_inbox else args.inbox,
        enable_clipboard=args.clipboard,
    )

    def sig_handler(signum, frame):
        print("\nShutting down bridge daemon...")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    print("=" * 70)
    print(f"ChatGPT-to-Local Agent Bridge Daemon running at http://{args.host}:{args.port}")
    print(f"Authentication Token: {daemon.auth_token}")
    print(f"Allowed Workspaces: {daemon.allowed_roots}")
    print(f"Executor: {args.executor.upper()}")
    print("=" * 70)

    daemon.start(block=True)


if __name__ == "__main__":
    main()
