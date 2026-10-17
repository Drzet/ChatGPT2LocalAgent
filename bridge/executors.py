"""Agent execution engine for ChatGPT-to-Local Agent Bridge.

Provides:
- BaseAgentExecutor abstract base class
- MockAgentExecutor: Deterministic multi-step agent simulation harness with file operations & verifications
- PIAgentExecutor: Subprocess integration for local PI CLI agent (`pi -p`)
- TaskRunnerPool: Thread pool managing asynchronous task dispatch and lifecycle
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import time
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

if __package__ is None or __package__ == "":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.schema_validator import validate_task_handoff
from bridge.security import (
    CommandPolicy,
    PathTraversalError,
    ProcessResult,
    SafeProcessRunner,
    validate_path,
)
from bridge.state_store import StateStore, TaskRecord, TaskState, utc_now_iso

logger = logging.getLogger("bridge.executors")


def compute_sha256(file_path: Union[str, Path]) -> str:
    """Calculate SHA256 hex digest of a file."""
    h = hashlib.sha256()
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return ""
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class BaseAgentExecutor(abc.ABC):
    """Abstract interface for local agent executors."""

    @abc.abstractmethod
    def execute(
        self,
        task_payload: Dict[str, Any],
        state_store: StateStore,
        event_broadcaster: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Execute a task and return final feedback dictionary."""
        pass

    @abc.abstractmethod
    def cancel(self, task_id: str) -> bool:
        """Cancel an ongoing execution for a task."""
        pass

    @abc.abstractmethod
    def is_running(self, task_id: str) -> bool:
        """Check if a task is actively running."""
        pass


class MockAgentExecutor(BaseAgentExecutor):
    """Deterministic agent runner simulating multi-step operations and verifications."""

    def __init__(self, allowed_roots: Optional[List[Union[str, Path]]] = None):
        self.allowed_roots = [Path(r).resolve() for r in allowed_roots] if allowed_roots else [Path.cwd().resolve()]
        self._running_tasks: Dict[str, bool] = {}
        self._cancelled_tasks: set[str] = set()
        self.process_runner = SafeProcessRunner(CommandPolicy(allow_all=False))

    def cancel(self, task_id: str) -> bool:
        """Mark task as cancelled."""
        self._cancelled_tasks.add(task_id)
        if task_id in self._running_tasks:
            self._running_tasks[task_id] = False
            return True
        return False

    def is_running(self, task_id: str) -> bool:
        return self._running_tasks.get(task_id, False)

    def _emit(
        self,
        state_store: StateStore,
        broadcaster: Optional[Callable[[Dict[str, Any]], None]],
        task_id: str,
        state: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        evt = state_store.record_event(task_id, state=state, summary=summary)
        if broadcaster:
            try:
                broadcaster(evt)
            except Exception as e:
                logger.warning("Event broadcast failed: %s", e)
        return evt

    def execute(
        self,
        task_payload: Dict[str, Any],
        state_store: StateStore,
        event_broadcaster: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        task_info = task_payload.get("task", {})
        task_id = task_info.get("task_id", "unknown-task")
        title = task_info.get("title", "Mock Task")
        constraints = task_payload.get("constraints", {})
        env_conf = constraints.get("environment", {})
        workspace_str = env_conf.get("cwd", ".")
        sec_policy = constraints.get("security_policy", {})
        execution_mode = sec_policy.get("execution_mode", "sandboxed")
        limits = constraints.get("limits", {})
        timeout_sec = limits.get("max_execution_timeout_seconds", 300)

        self._running_tasks[task_id] = True
        self._cancelled_tasks.discard(task_id)

        task_rec = state_store.get_task(task_id)
        if task_rec is None:
            state_store.create_task(task_payload, initial_state=TaskState.VALIDATING)

        try:
            # 1. State: VALIDATING
            state_store.update_task_state(
                task_id=task_id,
                state=TaskState.VALIDATING,
                summary="Validating payload and workspace boundary security",
                progress={"percent": 5, "current_step": "validating_payload", "total_steps": 10, "completed_steps": 0},
            )
            self._emit(state_store, event_broadcaster, task_id, TaskState.VALIDATING, "Validating task payload")

            # Validate workspace directory
            try:
                ws_root = validate_path(workspace_str, self.allowed_roots, allow_create=True)
            except PathTraversalError as pte:
                err = {
                    "code": "ERR_FORBIDDEN_PATH",
                    "message": str(pte),
                    "is_fatal": True,
                }
                state_store.append_log(task_id, f"Security violation: {pte}", level="error", source="system")
                state_store.update_task_state(task_id=task_id, state=TaskState.FAILED, summary="Path validation failed", error=err)
                return self._emit(state_store, event_broadcaster, task_id, TaskState.FAILED, "Workspace security violation")

            # Handle interactive approval mode
            if execution_mode == "interactive_approval" or task_payload.get("require_confirmation", False):
                state_store.update_task_state(
                    task_id=task_id,
                    state=TaskState.WAITING_CONFIRMATION,
                    summary="Task requires human-in-the-loop approval before proceeding",
                    progress={"percent": 10, "current_step": "awaiting_approval", "total_steps": 10, "completed_steps": 1},
                )
                self._emit(state_store, event_broadcaster, task_id, TaskState.WAITING_CONFIRMATION, "Awaiting user confirmation")
                return state_store.build_feedback_event(task_id)

            # 2. State: RUNNING
            state_store.update_task_state(
                task_id=task_id,
                state=TaskState.RUNNING,
                summary=f"Running mock agent for: {title}",
                progress={"percent": 15, "current_step": "planning_execution", "total_steps": 10, "completed_steps": 1},
            )
            self._emit(state_store, event_broadcaster, task_id, TaskState.RUNNING, "Starting mock agent execution")

            state_store.append_log(
                task_id,
                f"Analyzing task objectives: '{task_info.get('description', '')}'",
                level="info",
                source="agent",
            )

            file_ops = task_payload.get("file_operations", [])
            total_ops = len(file_ops)
            completed_ops = 0

            # 3. File Operations
            for idx, op_item in enumerate(file_ops):
                if task_id in self._cancelled_tasks:
                    state_store.update_task_state(task_id, TaskState.CANCELLED, summary="Task execution cancelled by user")
                    return self._emit(state_store, event_broadcaster, task_id, TaskState.CANCELLED, "Execution cancelled")

                rel_path = op_item.get("path", "")
                op_type = op_item.get("operation", "modify")
                is_critical = op_item.get("is_critical", True)
                content = op_item.get("content", "")
                target_rename = op_item.get("target_path")

                try:
                    target_abs_path = validate_path(ws_root / rel_path, [ws_root], allow_create=True)
                except PathTraversalError as pte:
                    err = {
                        "code": "ERR_FORBIDDEN_PATH",
                        "message": f"File op '{rel_path}' violates workspace jail: {pte}",
                        "is_fatal": is_critical,
                    }
                    state_store.append_log(task_id, str(pte), level="error", source="system")
                    if is_critical:
                        state_store.update_task_state(task_id, TaskState.FAILED, summary="File path escaped sandbox", error=err)
                        return self._emit(state_store, event_broadcaster, task_id, TaskState.FAILED, "File path traversal error")
                    continue

                sha_before = compute_sha256(target_abs_path) if target_abs_path.exists() else ""
                state_store.append_log(
                    task_id,
                    f"Processing file operation [{op_type}] on '{rel_path}' (mode={execution_mode})",
                    level="info",
                    source="agent",
                )

                if execution_mode != "dry_run":
                    if op_type in ("create", "modify"):
                        target_abs_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(target_abs_path, "w", encoding="utf-8") as f:
                            f.write(content)
                    elif op_type == "delete":
                        if target_abs_path.exists():
                            if target_abs_path.is_dir():
                                shutil.rmtree(target_abs_path)
                            else:
                                target_abs_path.unlink()
                    elif op_type == "rename" and target_rename:
                        new_target = validate_path(ws_root / target_rename, [ws_root], allow_create=True)
                        new_target.parent.mkdir(parents=True, exist_ok=True)
                        if target_abs_path.exists():
                            target_abs_path.rename(new_target)
                    elif op_type == "inspect":
                        pass  # Read-only verification

                sha_after = compute_sha256(target_abs_path) if target_abs_path.exists() else ""
                size_bytes = target_abs_path.stat().st_size if target_abs_path.exists() else 0

                state_store.record_artifact(
                    task_id=task_id,
                    path=rel_path,
                    artifact_type="source_code",
                    size_bytes=size_bytes,
                    sha256=sha_after,
                    description=op_item.get("description", f"Mock {op_type} for {rel_path}"),
                )

                completed_ops += 1
                prog_pct = 20 + int(40 * (completed_ops / max(1, total_ops)))
                state_store.update_task_state(
                    task_id=task_id,
                    state=TaskState.RUNNING,
                    summary=f"Processed file {completed_ops}/{total_ops}: {rel_path}",
                    progress={
                        "percent": prog_pct,
                        "current_step": f"file_op_{op_type}",
                        "total_steps": 10,
                        "completed_steps": 2 + idx,
                    },
                )
                self._emit(state_store, event_broadcaster, task_id, TaskState.RUNNING)

            # 4. Automated Verification Checks
            ver_conf = task_payload.get("verification", {})
            checks = ver_conf.get("automated_checks", [])
            total_checks = len(checks)
            passed_checks = 0

            for idx, check in enumerate(checks):
                if task_id in self._cancelled_tasks:
                    state_store.update_task_state(task_id, TaskState.CANCELLED, summary="Task cancelled")
                    return self._emit(state_store, event_broadcaster, task_id, TaskState.CANCELLED, "Cancelled")

                check_id = check.get("id", f"check-{idx}")
                name = check.get("name", "Assertion check")
                check_type = check.get("type", "command")
                is_blocking = check.get("is_blocking", True)

                check_start = time.time()
                status = "passed"
                exit_code = 0
                stdout_snip = ""
                stderr_snip = ""
                failure_reason = ""

                state_store.append_log(
                    task_id,
                    f"Executing verification assertion [{check_type}]: {name}",
                    level="info",
                    source="verifier",
                )

                if check_type == "file_exists":
                    target_p = ws_root / check.get("target_path", "")
                    if not target_p.exists():
                        status = "failed"
                        failure_reason = f"Target file does not exist: {target_p}"
                elif check_type == "file_contains":
                    target_p = ws_root / check.get("target_path", "")
                    pattern = check.get("pattern", "")
                    if not target_p.exists():
                        status = "failed"
                        failure_reason = f"File not found: {target_p}"
                    else:
                        with open(target_p, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                        if pattern not in text:
                            status = "failed"
                            failure_reason = f"File does not contain expected string: '{pattern}'"
                elif check_type == "file_matches_regex":
                    target_p = ws_root / check.get("target_path", "")
                    pattern = check.get("pattern", "")
                    if not target_p.exists():
                        status = "failed"
                        failure_reason = f"File not found: {target_p}"
                    else:
                        with open(target_p, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                        if not re.search(pattern, text):
                            status = "failed"
                            failure_reason = f"File does not match regex: '{pattern}'"
                elif check_type in ("command", "test_suite"):
                    cmd_str = check.get("command", "echo ok")
                    argv = shlex.split(cmd_str)
                    if execution_mode == "dry_run":
                        stdout_snip = "[dry_run] Simulated command execution"
                    else:
                        res = self.process_runner.run(
                            argv=argv,
                            cwd=ws_root,
                            timeout_seconds=check.get("timeout_seconds", 60),
                        )
                        exit_code = res.exit_code
                        stdout_snip = res.stdout[:500]
                        stderr_snip = res.stderr[:500]

                        expected_code = check.get("expected_exit_code", 0)
                        if res.timed_out:
                            status = "timed_out"
                            failure_reason = f"Command timed out after {check.get('timeout_seconds', 60)}s"
                        elif exit_code != expected_code:
                            status = "failed"
                            failure_reason = f"Command exited with code {exit_code}, expected {expected_code}"

                        for req_str in check.get("expected_stdout_contains", []):
                            if req_str not in res.stdout:
                                status = "failed"
                                failure_reason = f"Stdout missing required string: '{req_str}'"

                        for bad_str in check.get("forbidden_stderr_contains", []):
                            if bad_str in res.stderr:
                                status = "failed"
                                failure_reason = f"Stderr contains forbidden string: '{bad_str}'"

                duration_ms = int((time.time() - check_start) * 1000)
                state_store.record_verification_result(
                    task_id=task_id,
                    step_id=check_id,
                    name=name,
                    status=status,
                    duration_ms=duration_ms,
                    exit_code=exit_code,
                    stdout_snippet=stdout_snip,
                    stderr_snippet=stderr_snip,
                    failure_reason=failure_reason or None,
                )

                if status == "passed":
                    passed_checks += 1
                elif is_blocking:
                    err = {
                        "code": "ERR_VERIFICATION_FAILED",
                        "message": f"Verification check '{name}' failed: {failure_reason}",
                        "is_fatal": True,
                    }
                    state_store.append_log(task_id, f"Verification failed: {failure_reason}", level="error", source="verifier")
                    state_store.update_task_state(task_id, TaskState.FAILED, summary=f"Verification failure on '{name}'", error=err)
                    return self._emit(state_store, event_broadcaster, task_id, TaskState.FAILED, "Automated check failure")

            # 5. Success
            state_store.update_task_state(
                task_id=task_id,
                state=TaskState.SUCCEEDED,
                summary=f"Task completed successfully. {passed_checks}/{total_checks} verifications passed.",
                progress={"percent": 100, "current_step": "completed", "total_steps": 10, "completed_steps": 10},
                exit_code=0,
            )
            state_store.append_log(
                task_id,
                "All file modifications and verification checks passed successfully.",
                level="info",
                source="agent",
            )
            return self._emit(state_store, event_broadcaster, task_id, TaskState.SUCCEEDED, "Task execution succeeded")

        except Exception as exc:
            logger.exception("Unexpected error in MockAgentExecutor: %s", exc)
            err = {
                "code": "ERR_INTERNAL",
                "message": f"Internal execution error: {exc}",
                "is_fatal": True,
            }
            state_store.append_log(task_id, f"Internal crash: {exc}", level="error", source="system")
            state_store.update_task_state(task_id, TaskState.FAILED, summary=f"Agent internal error: {exc}", error=err)
            return self._emit(state_store, event_broadcaster, task_id, TaskState.FAILED, "Internal error")
        finally:
            self._running_tasks[task_id] = False


class PIAgentExecutor(BaseAgentExecutor):
    """Local PI agent CLI (`/opt/homebrew/bin/pi` or configurable binary) executor."""

    def __init__(
        self,
        pi_binary_path: Optional[str] = None,
        allowed_roots: Optional[List[Union[str, Path]]] = None,
    ):
        self.pi_binary = pi_binary_path or os.environ.get("PI_BINARY_PATH", "/opt/homebrew/bin/pi")
        self.allowed_roots = [Path(r).resolve() for r in allowed_roots] if allowed_roots else [Path.cwd().resolve()]
        self._running_tasks: Dict[str, bool] = {}
        self._cancelled_tasks: set[str] = set()
        self.process_runner = SafeProcessRunner(CommandPolicy(allow_all=True))

    def cancel(self, task_id: str) -> bool:
        self._cancelled_tasks.add(task_id)
        if task_id in self._running_tasks:
            self._running_tasks[task_id] = False
            return True
        return False

    def is_running(self, task_id: str) -> bool:
        return self._running_tasks.get(task_id, False)

    def _format_pi_prompt(self, task_payload: Dict[str, Any]) -> str:
        """Format task payload into structured markdown prompt for non-interactive pi execution."""
        task = task_payload.get("task", {})
        context = task_payload.get("context", {})
        file_ops = task_payload.get("file_operations", [])
        verification = task_payload.get("verification", {})

        prompt_lines = [
            f"# Task: {task.get('title', 'Local Task')}",
            f"**Task ID**: {task.get('task_id', '')}",
            f"**Category**: {task.get('category', 'feature')} | **Priority**: {task.get('priority', 'medium')}",
            "",
            "## Objective & User Intent",
            context.get("user_intent", "Execute the requested changes."),
            "",
            "## Architecture Decisions & Rationale",
            context.get("decision_rationale", "Follow existing project architecture and code layout."),
            "",
            "## Detailed Instructions",
            task.get("description", ""),
            "",
        ]

        if file_ops:
            prompt_lines.append("## Target File Operations")
            for op in file_ops:
                prompt_lines.append(f"- **{op.get('operation', 'modify')}** `{op.get('path', '')}`: {op.get('description', '')}")
                if op.get("content"):
                    prompt_lines.append(f"```\n{op.get('content')}\n```")
            prompt_lines.append("")

        if verification.get("acceptance_statement"):
            prompt_lines.append("## Acceptance Criteria")
            prompt_lines.append(verification.get("acceptance_statement"))
            prompt_lines.append("")

        return "\n".join(prompt_lines)

    def _build_argv(self, binary: str, prompt: str) -> List[str]:
        """Build PI's noninteractive command without shell interpolation."""
        return [binary, "-p", prompt, "--tools", "read,edit,write,bash,grep,find,ls", "--approve"]

    def execute(
        self,
        task_payload: Dict[str, Any],
        state_store: StateStore,
        event_broadcaster: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        task_info = task_payload.get("task", {})
        task_id = task_info.get("task_id", "pi-task")
        title = task_info.get("title", "PI Task")
        constraints = task_payload.get("constraints", {})
        env_conf = constraints.get("environment", {})
        workspace_str = env_conf.get("cwd", ".")
        sec_policy = constraints.get("security_policy", {})
        execution_mode = sec_policy.get("execution_mode", "sandboxed")
        limits = constraints.get("limits", {})
        timeout_sec = limits.get("max_execution_timeout_seconds", 600)

        self._running_tasks[task_id] = True
        self._cancelled_tasks.discard(task_id)

        task_rec = state_store.get_task(task_id)
        if task_rec is None:
            state_store.create_task(task_payload, initial_state=TaskState.VALIDATING)

        try:
            # 1. State: VALIDATING
            state_store.update_task_state(
                task_id=task_id,
                state=TaskState.VALIDATING,
                summary="Validating workspace boundary for PI agent",
                progress={"percent": 5, "current_step": "validating", "total_steps": 10, "completed_steps": 0},
            )
            state_store.record_event(task_id, TaskState.VALIDATING, "Validating PI execution workspace")

            try:
                ws_root = validate_path(workspace_str, self.allowed_roots, allow_create=True)
            except PathTraversalError as pte:
                err = {"code": "ERR_FORBIDDEN_PATH", "message": str(pte), "is_fatal": True}
                state_store.append_log(task_id, str(pte), level="error", source="system")
                state_store.update_task_state(task_id, TaskState.FAILED, summary="Path validation failed", error=err)
                return state_store.record_event(task_id, TaskState.FAILED, "Workspace security violation")

            # Check if PI binary exists
            pi_path = shutil.which(self.pi_binary) or self.pi_binary
            if not os.path.exists(pi_path) and not shutil.which(self.pi_binary):
                err = {
                    "code": "ERR_DEPENDENCY_MISSING",
                    "message": f"PI binary not found at '{self.pi_binary}'. Install PI or configure PI_BINARY_PATH.",
                    "is_fatal": True,
                }
                state_store.append_log(task_id, f"Missing PI binary: {self.pi_binary}", level="error", source="system")
                state_store.update_task_state(task_id, TaskState.FAILED, summary="PI binary not found", error=err)
                return state_store.record_event(task_id, TaskState.FAILED, "PI binary missing")

            # Handle interactive approval mode
            if execution_mode == "interactive_approval" or task_payload.get("require_confirmation", False):
                state_store.update_task_state(
                    task_id=task_id,
                    state=TaskState.WAITING_CONFIRMATION,
                    summary="PI Task requires human approval before execution",
                    progress={"percent": 10, "current_step": "awaiting_approval", "total_steps": 10, "completed_steps": 1},
                )
                return state_store.record_event(task_id, TaskState.WAITING_CONFIRMATION, "Awaiting approval")

            # 2. State: RUNNING
            state_store.update_task_state(
                task_id=task_id,
                state=TaskState.RUNNING,
                summary=f"Invoking PI Agent: {title}",
                progress={"percent": 20, "current_step": "invoking_pi", "total_steps": 10, "completed_steps": 2},
            )
            state_store.record_event(task_id, TaskState.RUNNING, "Starting PI agent execution")

            prompt = self._format_pi_prompt(task_payload)
            argv = self._build_argv(pi_path, prompt)

            def on_stdout(line: str):
                clean = line.strip()
                if clean:
                    state_store.append_log(task_id, clean, level="info", source="stdout")

            def on_stderr(line: str):
                clean = line.strip()
                if clean:
                    state_store.append_log(task_id, clean, level="warn", source="stderr")

            state_store.append_log(task_id, f"Launching PI process in {ws_root}", level="info", source="system")
            proc_res = self.process_runner.run(
                argv=argv,
                cwd=ws_root,
                timeout_seconds=timeout_sec,
                stdout_callback=on_stdout,
                stderr_callback=on_stderr,
            )

            if proc_res.timed_out:
                err = {"code": "ERR_TIMEOUT", "message": f"PI agent execution timed out after {timeout_sec}s", "is_fatal": True}
                state_store.update_task_state(task_id, TaskState.FAILED, summary="PI execution timed out", error=err)
                return state_store.record_event(task_id, TaskState.FAILED, "PI timeout")

            if proc_res.exit_code != 0:
                err = {
                    "code": "ERR_AGENT_CRASH",
                    "message": f"PI agent exited with non-zero status code: {proc_res.exit_code}",
                    "is_fatal": True,
                }
                state_store.update_task_state(task_id, TaskState.FAILED, summary=f"PI exited with code {proc_res.exit_code}", error=err)
                return state_store.record_event(task_id, TaskState.FAILED, "PI exited with error")

            # 3. Post-execution verifications
            ver_conf = task_payload.get("verification", {})
            checks = ver_conf.get("automated_checks", [])
            for idx, check in enumerate(checks):
                check_id = check.get("id", f"check-{idx}")
                name = check.get("name", "Verification assertion")
                check_type = check.get("type", "command")
                is_blocking = check.get("is_blocking", True)

                check_start = time.time()
                status = "passed"
                exit_code = 0
                stdout_snip = ""
                stderr_snip = ""
                failure_reason = ""

                if check_type == "file_exists":
                    p = ws_root / check.get("target_path", "")
                    if not p.exists():
                        status = "failed"
                        failure_reason = f"File missing: {p}"
                elif check_type in ("command", "test_suite"):
                    c_argv = shlex.split(check.get("command", "echo ok"))
                    c_res = self.process_runner.run(c_argv, cwd=ws_root, timeout_seconds=check.get("timeout_seconds", 60))
                    exit_code = c_res.exit_code
                    stdout_snip = c_res.stdout[:500]
                    stderr_snip = c_res.stderr[:500]
                    if exit_code != check.get("expected_exit_code", 0):
                        status = "failed"
                        failure_reason = f"Check command exited with code {exit_code}"

                dur_ms = int((time.time() - check_start) * 1000)
                state_store.record_verification_result(
                    task_id=task_id,
                    step_id=check_id,
                    name=name,
                    status=status,
                    duration_ms=dur_ms,
                    exit_code=exit_code,
                    stdout_snippet=stdout_snip,
                    stderr_snippet=stderr_snip,
                    failure_reason=failure_reason or None,
                )
                if status != "passed" and is_blocking:
                    err = {"code": "ERR_VERIFICATION_FAILED", "message": failure_reason, "is_fatal": True}
                    state_store.update_task_state(task_id, TaskState.FAILED, summary="Post-verification check failed", error=err)
                    return state_store.record_event(task_id, TaskState.FAILED, "Verification failed")

            # 4. Succeeded
            state_store.update_task_state(
                task_id=task_id,
                state=TaskState.SUCCEEDED,
                summary="PI agent completed task and passed all verifications",
                progress={"percent": 100, "current_step": "completed", "total_steps": 10, "completed_steps": 10},
                exit_code=0,
            )
            return state_store.record_event(task_id, TaskState.SUCCEEDED, "PI agent completed successfully")

        except Exception as exc:
            logger.exception("Unexpected error in PIAgentExecutor: %s", exc)
            err = {"code": "ERR_INTERNAL", "message": str(exc), "is_fatal": True}
            state_store.update_task_state(task_id, TaskState.FAILED, summary=f"Internal error: {exc}", error=err)
            return state_store.record_event(task_id, TaskState.FAILED, "Internal error")
        finally:
            self._running_tasks[task_id] = False


class OpenCodeExecutor(PIAgentExecutor):
    """Run OpenCode non-interactively against an explicitly configured model.

    The model MUST be specified; silently falling back to a cloud model could
    incur costs and violate the intended local-only workflow.
    """

    def __init__(
        self,
        opencode_binary_path: Optional[str] = None,
        model: Optional[str] = None,
        allowed_roots: Optional[List[Union[str, Path]]] = None,
    ):
        super().__init__(
            pi_binary_path=opencode_binary_path or os.environ.get("OPENCODE_BINARY", "opencode"),
            allowed_roots=allowed_roots,
        )
        self.model = model or os.environ.get("OPENCODE_MODEL")

    def _build_argv(self, binary: str, prompt: str) -> List[str]:
        if not self.model:
            raise ValueError(
                "OpenCode model not configured; use --opencode-model or OPENCODE_MODEL "
                "with an explicit local provider/model identifier"
            )
        return [binary, "run", "--model", self.model, prompt]


class TaskRunnerPool:
    """Thread pool manager for executing tasks asynchronously."""

    def __init__(self, executor: BaseAgentExecutor, state_store: StateStore, max_workers: int = 2):
        self.executor = executor
        self.state_store = state_store
        self.max_workers = max_workers
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="task_runner")
        self._futures: Dict[str, Future] = {}
        self._event_subscribers: List[Callable[[Dict[str, Any]], None]] = []

    def add_event_subscriber(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register a callback to receive real-time execution events."""
        self._event_subscribers.append(callback)

    def _broadcast(self, event: Dict[str, Any]) -> None:
        for cb in list(self._event_subscribers):
            try:
                cb(event)
            except Exception as e:
                logger.debug("Broadcast error: %s", e)

    def submit_task(self, payload: Dict[str, Any]) -> TaskRecord:
        """Submit a task to the queue and pool."""
        task_info = payload.get("task", {})
        task_id = task_info.get("task_id")
        record = self.state_store.create_task(payload, initial_state=TaskState.QUEUED)
        task_id = record.task_id

        def worker_job():
            try:
                self.executor.execute(
                    task_payload=payload,
                    state_store=self.state_store,
                    event_broadcaster=self._broadcast,
                )
            except Exception as exc:
                logger.exception("Task runner error on task %s: %s", task_id, exc)

        future = self._pool.submit(worker_job)
        self._futures[task_id] = future
        return record

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a queued or executing task."""
        if task_id in self._futures:
            future = self._futures[task_id]
            if not future.running():
                future.cancel()
        cancelled = self.executor.cancel(task_id)
        self.state_store.update_task_state(
            task_id=task_id,
            state=TaskState.CANCELLED,
            summary="Task cancelled by user request",
        )
        self._broadcast(self.state_store.build_feedback_event(task_id, custom_state=TaskState.CANCELLED))
        return cancelled

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown thread pool."""
        self._pool.shutdown(wait=wait)
