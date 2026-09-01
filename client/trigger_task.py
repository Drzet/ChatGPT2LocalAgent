#!/usr/bin/env python3
"""CLI client for interacting with ChatGPT-to-Local Agent Bridge Daemon.

Supports:
- Submitting task handoff payloads (with optional SSE live streaming)
- Querying task status and execution results
- Streaming real-time execution logs
- Confirming paused tasks awaiting human approval
- Cancelling running tasks
- Listing recent tasks
- Healthcheck probes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


# ANSI color formatting
class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"


def get_token(cli_token: Optional[str]) -> str:
    """Resolve authentication token from CLI arg, environment, or default file."""
    if cli_token:
        return cli_token
    env_tok = os.environ.get("BRIDGE_AUTH_TOKEN")
    if env_tok:
        return env_tok
    token_file = Path.home() / ".chatgpt_local_bridge" / "token"
    if token_file.exists():
        return token_file.read_text("utf-8").strip()
    return ""


def make_request(
    url: str,
    method: str = "GET",
    token: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Any]:
    """Execute HTTP request using standard library urllib."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "ChatGPT-Local-Bridge-CLI/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body_bytes = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30.0) as resp:
            status = resp.status
            content = resp.read().decode("utf-8")
            try:
                parsed = json.loads(content)
            except Exception:
                parsed = content
            return status, parsed
    except urllib.error.HTTPError as he:
        err_content = he.read().decode("utf-8")
        try:
            parsed_err = json.loads(err_content)
        except Exception:
            parsed_err = err_content
        return he.code, parsed_err
    except Exception as exc:
        return 0, {"error": "NETWORK_ERROR", "message": str(exc)}


def stream_sse_events(base_url: str, task_id: str, token: Optional[str] = None) -> int:
    """Stream real-time SSE events for a task and print live terminal updates."""
    events_url = f"{base_url.rstrip('/')}/api/tasks/{task_id}/events"
    if token:
        events_url += f"?token={urllib.parse.quote(token)}"

    headers = {
        "Accept": "text/event-stream",
        "User-Agent": "ChatGPT-Local-Bridge-CLI/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(events_url, headers=headers, method="GET")
    print(f"{Color.CYAN}Connecting to live event stream for task {Color.BOLD}{task_id}{Color.RESET}...")

    seen_logs: set[str] = set()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=600.0) as resp:
            buffer = ""
            for raw_chunk in iter(lambda: resp.read(128), b""):
                chunk = raw_chunk.decode("utf-8", errors="replace")
                buffer += chunk
                while "\n\n" in buffer:
                    message_block, buffer = buffer.split("\n\n", 1)
                    for line in message_block.splitlines():
                        line = line.strip()
                        if line.startswith("data:"):
                            json_str = line[5:].strip()
                            try:
                                event = json.loads(json_str)
                                state = event.get("state", "").upper()
                                summary = event.get("summary", "")
                                progress = event.get("progress", {})
                                pct = progress.get("percent", 0)

                                state_color = Color.CYAN
                                if state == "SUCCEEDED":
                                    state_color = Color.GREEN
                                elif state in ("FAILED", "BLOCKED"):
                                    state_color = Color.RED
                                elif state == "WAITING_CONFIRMATION":
                                    state_color = Color.YELLOW

                                print(
                                    f"[{state_color}{state:<14}{Color.RESET}] "
                                    f"{Color.BOLD}{pct:>3}%{Color.RESET} - {summary}"
                                )

                                # Print new logs if included
                                for log in event.get("logs", []):
                                    log_key = f"{log.get('timestamp')}:{log.get('message')}"
                                    if log_key not in seen_logs:
                                        seen_logs.add(log_key)
                                        lvl = log.get("level", "info").upper()
                                        src = log.get("source", "agent")
                                        msg = log.get("message", "")
                                        print(f"  {Color.DIM}[{src}:{lvl}]{Color.RESET} {msg}")

                                # Print verification checks
                                for vr in event.get("verification_results", []):
                                    vr_name = vr.get("name", "")
                                    vr_status = vr.get("status", "")
                                    v_col = Color.GREEN if vr_status == "passed" else Color.RED
                                    print(f"  ↳ Check {Color.BOLD}{vr_name}{Color.RESET}: [{v_col}{vr_status.upper()}{Color.RESET}]")

                                if state in ("SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED"):
                                    return 0 if state == "SUCCEEDED" else 1

                            except Exception:
                                pass
    except urllib.error.HTTPError as he:
        print(f"{Color.RED}SSE stream error: HTTP {he.code}{Color.RESET}")
        return 1
    except KeyboardInterrupt:
        print(f"\n{Color.YELLOW}Detached from live stream (task continues running in background).{Color.RESET}")
        return 0
    except Exception as exc:
        print(f"{Color.RED}Stream disconnected: {exc}{Color.RESET}")
        return 1

    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    """Submit a task payload to the daemon."""
    token = get_token(args.token)
    payload_file = Path(args.file)
    if not payload_file.exists():
        print(f"{Color.RED}Error: File '{args.file}' not found.{Color.RESET}")
        return 1

    try:
        payload = json.loads(payload_file.read_text("utf-8"))
    except Exception as e:
        print(f"{Color.RED}Error: Failed to parse JSON in '{args.file}': {e}{Color.RESET}")
        return 1

    url = f"{args.url.rstrip('/')}/api/tasks"
    status, resp = make_request(url, method="POST", token=token, data=payload)

    if status in (201, 202):
        task_id = resp.get("task_id", "")
        task_status = resp.get("status", "")
        print(f"{Color.GREEN}Task submitted successfully!{Color.RESET}")
        print(f"  Task ID: {Color.BOLD}{task_id}{Color.RESET}")
        print(f"  Status:  {task_status}")
        if resp.get("confirmation_token"):
            print(f"  Confirmation Token: {Color.YELLOW}{resp['confirmation_token']}{Color.RESET}")

        if args.stream or args.wait:
            return stream_sse_events(args.url, task_id, token)
        return 0
    else:
        print(f"{Color.RED}Submission failed (HTTP {status}):{Color.RESET}")
        print(json.dumps(resp, indent=2))
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Query current status of a task."""
    token = get_token(args.token)
    url = f"{args.url.rstrip('/')}/api/tasks/{args.task_id}"
    status, resp = make_request(url, method="GET", token=token)

    if status == 200:
        print(json.dumps(resp, indent=2))
        return 0
    else:
        print(f"{Color.RED}Error (HTTP {status}):{Color.RESET}")
        print(json.dumps(resp, indent=2))
        return 1


def cmd_logs(args: argparse.Namespace) -> int:
    """Fetch logs for a task."""
    token = get_token(args.token)
    if args.tail:
        return stream_sse_events(args.url, args.task_id, token)

    url = f"{args.url.rstrip('/')}/api/tasks/{args.task_id}/logs"
    status, resp = make_request(url, method="GET", token=token)
    if status == 200:
        logs = resp.get("logs", [])
        print(f"Logs for task {Color.BOLD}{args.task_id}{Color.RESET} ({len(logs)} entries):")
        for log in logs:
            ts = log.get("timestamp", "")
            lvl = log.get("level", "info").upper()
            src = log.get("source", "agent")
            msg = log.get("message", "")
            print(f"[{ts}] [{src}:{lvl}] {msg}")
        return 0
    else:
        print(f"{Color.RED}Error (HTTP {status}): {resp}{Color.RESET}")
        return 1


def cmd_confirm(args: argparse.Namespace) -> int:
    """Approve a paused task waiting for confirmation."""
    token = get_token(args.token)
    url = f"{args.url.rstrip('/')}/api/tasks/{args.task_id}/confirm"
    body = {"token": args.confirm_token} if args.confirm_token else None
    status, resp = make_request(url, method="POST", token=token, data=body)
    if status == 200:
        print(f"{Color.GREEN}Task {args.task_id} approved and resumed!{Color.RESET}")
        return 0
    else:
        print(f"{Color.RED}Failed to confirm task (HTTP {status}): {resp}{Color.RESET}")
        return 1


def cmd_cancel(args: argparse.Namespace) -> int:
    """Cancel an active task."""
    token = get_token(args.token)
    url = f"{args.url.rstrip('/')}/api/tasks/{args.task_id}/cancel"
    status, resp = make_request(url, method="POST", token=token)
    if status == 200:
        print(f"{Color.YELLOW}Task {args.task_id} cancelled.{Color.RESET}")
        return 0
    else:
        print(f"{Color.RED}Failed to cancel task (HTTP {status}): {resp}{Color.RESET}")
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    """List recent tasks."""
    token = get_token(args.token)
    url = f"{args.url.rstrip('/')}/api/tasks?limit={args.limit}"
    if args.state:
        url += f"&state={args.state}"
    status, resp = make_request(url, method="GET", token=token)
    if status == 200:
        tasks = resp.get("tasks", [])
        print(f"{Color.BOLD}Recent Tasks ({len(tasks)}):{Color.RESET}")
        print(f"{'Task ID':<25} {'State':<20} {'Mode':<12} {'Title'}")
        print("-" * 75)
        for t in tasks:
            tid = t.get("task_id", "")
            st = t.get("state", "")
            mode = t.get("execution_mode", "")
            title = t.get("title", "")
            print(f"{tid:<25} {st:<20} {mode:<12} {title}")
        return 0
    else:
        print(f"{Color.RED}Error (HTTP {status}): {resp}{Color.RESET}")
        return 1


def cmd_health(args: argparse.Namespace) -> int:
    """Probe daemon health."""
    url = f"{args.url.rstrip('/')}/health"
    status, resp = make_request(url, method="GET")
    if status == 200:
        print(f"{Color.GREEN}Bridge Daemon is HEALTHY{Color.RESET}")
        print(json.dumps(resp, indent=2))
        return 0
    else:
        print(f"{Color.RED}Bridge Daemon is UNHEALTHY (HTTP {status}){Color.RESET}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI Client for ChatGPT-to-Local Agent Bridge")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="Bridge daemon base URL (default: http://127.0.0.1:8765)")
    parser.add_argument("--token", default=None, help="Authentication Bearer token")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # submit
    p_submit = subparsers.add_parser("submit", help="Submit task JSON payload")
    p_submit.add_argument("--file", "-f", required=True, help="Path to task handoff JSON file")
    p_submit.add_argument("--stream", "-s", action="store_true", help="Live-stream SSE logs and status")
    p_submit.add_argument("--wait", "-w", action="store_true", help="Wait until execution finishes")

    # status
    p_status = subparsers.add_parser("status", help="Get task execution status")
    p_status.add_argument("task_id", help="Task identifier")

    # logs
    p_logs = subparsers.add_parser("logs", help="Get or stream logs for a task")
    p_logs.add_argument("task_id", help="Task identifier")
    p_logs.add_argument("--tail", "-t", action="store_true", help="Tail live execution logs")

    # confirm
    p_confirm = subparsers.add_parser("confirm", help="Approve task in WAITING_CONFIRMATION state")
    p_confirm.add_argument("task_id", help="Task identifier")
    p_confirm.add_argument("--confirm-token", help="Optional confirmation approval token")

    # cancel
    p_cancel = subparsers.add_parser("cancel", help="Cancel task execution")
    p_cancel.add_argument("task_id", help="Task identifier")

    # list
    p_list = subparsers.add_parser("list", help="List recent tasks")
    p_list.add_argument("--limit", type=int, default=20, help="Maximum records to return")
    p_list.add_argument("--state", help="Filter by state (e.g. running, queued, succeeded)")

    # health
    subparsers.add_parser("health", help="Check Bridge Daemon health")

    args = parser.parse_args()

    commands = {
        "submit": cmd_submit,
        "status": cmd_status,
        "logs": cmd_logs,
        "confirm": cmd_confirm,
        "cancel": cmd_cancel,
        "list": cmd_list,
        "health": cmd_health,
    }

    handler = commands.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
