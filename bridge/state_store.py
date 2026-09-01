"""State Store and Persistence Engine for ChatGPT-to-Local Agent Bridge.

Thread-safe SQLite-backed storage tracking tasks across 8 lifecycle states,
event history, logs, artifacts, verification results, and confirmation gates.
"""

from __future__ import annotations

import datetime
import hmac
import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("bridge.state_store")


class TaskState:
    """Task lifecycle state enumeration."""
    RECEIVED = "received"
    VALIDATING = "validating"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    WAITING_USER_INPUT = "waiting_user_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"

    ALL_STATES = {
        RECEIVED,
        VALIDATING,
        QUEUED,
        RUNNING,
        WAITING_CONFIRMATION,
        WAITING_USER_INPUT,
        SUCCEEDED,
        FAILED,
        CANCELLED,
        BLOCKED,
    }


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class TaskRecord:
    """In-memory representation of a task record."""
    task_id: str
    session_id: str
    title: str
    state: str
    execution_mode: str
    workspace_root: str
    payload: Dict[str, Any]
    progress: Dict[str, Any]
    summary: str
    created_at: str
    exit_code: Optional[int] = None
    error: Optional[Dict[str, Any]] = None
    confirmation_token: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "title": self.title,
            "state": self.state,
            "execution_mode": self.execution_mode,
            "workspace_root": self.workspace_root,
            "progress": self.progress,
            "summary": self.summary,
            "exit_code": self.exit_code,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "payload": self.payload,
        }


@dataclass
class LogEntry:
    """Structured log record."""
    task_id: str
    timestamp: str
    level: str
    source: str
    message: str
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "timestamp": self.timestamp,
            "level": self.level,
            "source": self.source,
            "message": self.message,
        }
        if self.metadata:
            d["metadata"] = self.metadata
        return d


class StateStore:
    """Thread-safe SQLite storage engine for tasks, events, logs, and artifacts."""

    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0,
            )
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.execute("PRAGMA foreign_keys = ON;")
                if self.db_path != ":memory:":
                    self._conn.execute("PRAGMA journal_mode = WAL;")
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL,
                    execution_mode TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    progress_percent INTEGER DEFAULT 0,
                    progress_current_step TEXT DEFAULT '',
                    progress_total_steps INTEGER DEFAULT 0,
                    progress_completed_steps INTEGER DEFAULT 0,
                    progress_estimated_remaining_seconds INTEGER,
                    summary TEXT DEFAULT '',
                    exit_code INTEGER,
                    error_json TEXT,
                    confirmation_token TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS task_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata_json TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    state TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    feedback_json TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS task_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS verification_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    duration_ms INTEGER NOT NULL,
                    stdout_snippet TEXT,
                    stderr_snippet TEXT,
                    failure_reason TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
                CREATE INDEX IF NOT EXISTS idx_task_logs_task_id ON task_logs(task_id);
                CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id);
                CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_id ON task_artifacts(task_id);
                CREATE INDEX IF NOT EXISTS idx_verification_results_task_id ON verification_results(task_id);
                """
            )
            conn.commit()

    def create_task(self, payload: Dict[str, Any], initial_state: str = TaskState.RECEIVED) -> TaskRecord:
        """Create and store a new task record from validated task handoff payload."""
        task_info = payload.get("task", {})
        task_id = task_info.get("task_id") or f"task-{uuid.uuid4().hex[:12]}"
        title = task_info.get("title") or "Untitled Task"
        context = payload.get("context", {})
        session_id = context.get("session_id") or str(uuid.uuid4())
        constraints = payload.get("constraints", {})
        env = constraints.get("environment", {})
        workspace_root = env.get("cwd") or "."
        sec_policy = constraints.get("security_policy", {})
        execution_mode = sec_policy.get("execution_mode") or "sandboxed"

        created_at = context.get("timestamp") or utc_now_iso()
        summary = f"Task created in state {initial_state}"

        total_steps = len(payload.get("file_operations", [])) + len(
            payload.get("verification", {}).get("automated_checks", [])
        )
        if total_steps == 0:
            total_steps = 1

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks (
                    task_id, session_id, title, state, execution_mode, workspace_root,
                    payload_json, progress_percent, progress_current_step,
                    progress_total_steps, progress_completed_steps, summary,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    session_id,
                    title,
                    initial_state,
                    execution_mode,
                    workspace_root,
                    json.dumps(payload),
                    0,
                    "Task received",
                    total_steps,
                    0,
                    summary,
                    created_at,
                ),
            )
            conn.commit()

        record = self.get_task(task_id)
        if not record:
            raise RuntimeError(f"Failed to retrieve newly created task: {task_id}")

        self.record_event(
            task_id=task_id,
            state=initial_state,
            summary=summary,
        )
        return record

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """Fetch task record by ID."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
            if not row:
                return None

            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            error = json.loads(row["error_json"]) if row["error_json"] else None
            progress = {
                "percent": row["progress_percent"] or 0,
                "current_step": row["progress_current_step"] or "",
                "total_steps": row["progress_total_steps"] or 0,
                "completed_steps": row["progress_completed_steps"] or 0,
            }
            if row["progress_estimated_remaining_seconds"] is not None:
                progress["estimated_remaining_seconds"] = row["progress_estimated_remaining_seconds"]

            return TaskRecord(
                task_id=row["task_id"],
                session_id=row["session_id"] or "",
                title=row["title"],
                state=row["state"],
                execution_mode=row["execution_mode"],
                workspace_root=row["workspace_root"],
                payload=payload,
                progress=progress,
                summary=row["summary"] or "",
                exit_code=row["exit_code"],
                error=error,
                confirmation_token=row["confirmation_token"],
                created_at=row["created_at"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
            )

    def update_task_state(
        self,
        task_id: str,
        state: str,
        summary: Optional[str] = None,
        progress: Optional[Dict[str, Any]] = None,
        exit_code: Optional[int] = None,
        error: Optional[Dict[str, Any]] = None,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        """Update task state, progress, error, and timestamps atomically."""
        if state not in TaskState.ALL_STATES:
            logger.warning("Updating to unknown state: %s", state)

        now = utc_now_iso()
        fields: List[str] = ["state = ?"]
        params: List[Any] = [state]

        if summary is not None:
            fields.append("summary = ?")
            params.append(summary)

        if progress is not None:
            if "percent" in progress:
                fields.append("progress_percent = ?")
                params.append(progress["percent"])
            if "current_step" in progress:
                fields.append("progress_current_step = ?")
                params.append(progress["current_step"])
            if "total_steps" in progress:
                fields.append("progress_total_steps = ?")
                params.append(progress["total_steps"])
            if "completed_steps" in progress:
                fields.append("progress_completed_steps = ?")
                params.append(progress["completed_steps"])
            if "estimated_remaining_seconds" in progress:
                fields.append("progress_estimated_remaining_seconds = ?")
                params.append(progress["estimated_remaining_seconds"])

        if exit_code is not None:
            fields.append("exit_code = ?")
            params.append(exit_code)

        if error is not None:
            fields.append("error_json = ?")
            params.append(json.dumps(error))

        if started_at is not None:
            fields.append("started_at = ?")
            params.append(started_at)
        elif state == TaskState.RUNNING:
            fields.append("started_at = COALESCE(started_at, ?)")
            params.append(now)

        if finished_at is not None:
            fields.append("finished_at = ?")
            params.append(finished_at)
        elif state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.BLOCKED}:
            fields.append("finished_at = COALESCE(finished_at, ?)")
            params.append(now)

        params.append(task_id)
        query = f"UPDATE tasks SET {', '.join(fields)} WHERE task_id = ?"

        with self._lock:
            conn = self._get_conn()
            conn.execute(query, params)
            conn.commit()

        record = self.get_task(task_id)
        if record:
            self.record_event(
                task_id=task_id,
                state=state,
                summary=summary or record.summary,
            )
        return record

    def list_tasks(
        self,
        state: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[TaskRecord]:
        """List tasks with optional state filter and pagination."""
        with self._lock:
            conn = self._get_conn()
            if state:
                cur = conn.execute(
                    "SELECT * FROM tasks WHERE state = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (state, limit, offset),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            rows = cur.fetchall()

        results: List[TaskRecord] = []
        for row in rows:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            error = json.loads(row["error_json"]) if row["error_json"] else None
            progress = {
                "percent": row["progress_percent"] or 0,
                "current_step": row["progress_current_step"] or "",
                "total_steps": row["progress_total_steps"] or 0,
                "completed_steps": row["progress_completed_steps"] or 0,
            }
            if row["progress_estimated_remaining_seconds"] is not None:
                progress["estimated_remaining_seconds"] = row["progress_estimated_remaining_seconds"]

            results.append(
                TaskRecord(
                    task_id=row["task_id"],
                    session_id=row["session_id"] or "",
                    title=row["title"],
                    state=row["state"],
                    execution_mode=row["execution_mode"],
                    workspace_root=row["workspace_root"],
                    payload=payload,
                    progress=progress,
                    summary=row["summary"] or "",
                    exit_code=row["exit_code"],
                    error=error,
                    confirmation_token=row["confirmation_token"],
                    created_at=row["created_at"],
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                )
            )
        return results

    def append_log(
        self,
        task_id: str,
        message: str,
        level: str = "info",
        source: str = "agent",
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> LogEntry:
        """Append a structured log entry for a task."""
        ts = timestamp or utc_now_iso()
        meta_json = json.dumps(metadata) if metadata else None
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO task_logs (task_id, timestamp, level, source, message, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, ts, level, source, message, meta_json),
            )
            conn.commit()

        return LogEntry(
            task_id=task_id,
            timestamp=ts,
            level=level,
            source=source,
            message=message,
            metadata=metadata,
        )

    def get_logs(
        self,
        task_id: str,
        limit: int = 500,
        offset: int = 0,
    ) -> List[LogEntry]:
        """Fetch execution logs for a task."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """
                SELECT * FROM task_logs
                WHERE task_id = ?
                ORDER BY id ASC
                LIMIT ? OFFSET ?
                """,
                (task_id, limit, offset),
            )
            rows = cur.fetchall()

        entries: List[LogEntry] = []
        for row in rows:
            meta = json.loads(row["metadata_json"]) if row["metadata_json"] else None
            entries.append(
                LogEntry(
                    task_id=row["task_id"],
                    timestamp=row["timestamp"],
                    level=row["level"],
                    source=row["source"],
                    message=row["message"],
                    metadata=meta,
                )
            )
        return entries

    def record_artifact(
        self,
        task_id: str,
        path: str,
        artifact_type: str,
        size_bytes: int,
        sha256: str = "",
        description: str = "",
    ) -> Dict[str, Any]:
        """Record an artifact generated or modified during task execution."""
        now = utc_now_iso()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO task_artifacts (task_id, path, type, size_bytes, sha256, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, path, artifact_type, size_bytes, sha256, description, now),
            )
            conn.commit()

        return {
            "path": path,
            "type": artifact_type,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "description": description,
            "created_at": now,
        }

    def get_artifacts(self, task_id: str) -> List[Dict[str, Any]]:
        """Fetch all artifacts recorded for a task."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT * FROM task_artifacts WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            )
            rows = cur.fetchall()

        results: List[Dict[str, Any]] = []
        for row in rows:
            d: Dict[str, Any] = {
                "path": row["path"],
                "type": row["type"],
                "size_bytes": row["size_bytes"],
            }
            if row["sha256"]:
                d["sha256"] = row["sha256"]
            if row["description"]:
                d["description"] = row["description"]
            results.append(d)
        return results

    def record_verification_result(
        self,
        task_id: str,
        step_id: str,
        name: str,
        status: str,
        duration_ms: int,
        exit_code: Optional[int] = None,
        stdout_snippet: Optional[str] = None,
        stderr_snippet: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record automated check result for a task."""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO verification_results (
                    task_id, step_id, name, status, exit_code, duration_ms,
                    stdout_snippet, stderr_snippet, failure_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    step_id,
                    name,
                    status,
                    exit_code,
                    duration_ms,
                    stdout_snippet,
                    stderr_snippet,
                    failure_reason,
                ),
            )
            conn.commit()

        d: Dict[str, Any] = {
            "step_id": step_id,
            "name": name,
            "status": status,
            "duration_ms": duration_ms,
        }
        if exit_code is not None:
            d["exit_code"] = exit_code
        if stdout_snippet:
            d["stdout_snippet"] = stdout_snippet
        if stderr_snippet:
            d["stderr_snippet"] = stderr_snippet
        if failure_reason:
            d["failure_reason"] = failure_reason
        return d

    def get_verification_results(self, task_id: str) -> List[Dict[str, Any]]:
        """Fetch all verification check results for a task."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT * FROM verification_results WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            )
            rows = cur.fetchall()

        results: List[Dict[str, Any]] = []
        for row in rows:
            d: Dict[str, Any] = {
                "step_id": row["step_id"],
                "name": row["name"],
                "status": row["status"],
                "duration_ms": row["duration_ms"],
            }
            if row["exit_code"] is not None:
                d["exit_code"] = row["exit_code"]
            if row["stdout_snippet"]:
                d["stdout_snippet"] = row["stdout_snippet"]
            if row["stderr_snippet"]:
                d["stderr_snippet"] = row["stderr_snippet"]
            if row["failure_reason"]:
                d["failure_reason"] = row["failure_reason"]
            results.append(d)
        return results

    def record_event(
        self,
        task_id: str,
        state: Optional[str] = None,
        summary: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Construct, validate, and store a TaskFeedbackEvent."""
        eid = event_id or str(uuid.uuid4())
        ts = utc_now_iso()
        feedback = self.build_feedback_event(
            task_id=task_id,
            event_id=eid,
            custom_state=state,
            custom_summary=summary,
            timestamp=ts,
        )

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO task_events (event_id, task_id, timestamp, state, summary, feedback_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    eid,
                    task_id,
                    ts,
                    feedback["state"],
                    feedback["summary"],
                    json.dumps(feedback),
                ),
            )
            conn.commit()

        return feedback

    def get_events(self, task_id: str, since_id: int = 0) -> List[Dict[str, Any]]:
        """Fetch feedback events for a task, optionally starting after a specific event DB id."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """
                SELECT id, feedback_json FROM task_events
                WHERE task_id = ? AND id > ?
                ORDER BY id ASC
                """,
                (task_id, since_id),
            )
            rows = cur.fetchall()

        events: List[Dict[str, Any]] = []
        for row in rows:
            evt = json.loads(row["feedback_json"])
            evt["_db_id"] = row["id"]
            events.append(evt)
        return events

    def set_confirmation_token(self, task_id: str, token: str) -> None:
        """Set approval token required for a paused task."""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE tasks SET confirmation_token = ? WHERE task_id = ?",
                (token, task_id),
            )
            conn.commit()

    def verify_and_consume_confirmation(self, task_id: str, token: Optional[str] = None) -> bool:
        """Verify confirmation token and clear it if valid."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT confirmation_token, state FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = cur.fetchone()
            if not row:
                return False

            stored_token = row["confirmation_token"]
            state = row["state"]
            if state != TaskState.WAITING_CONFIRMATION:
                return False

            if stored_token is not None and stored_token != "":
                if token is None or not isinstance(token, str):
                    return False
                if not hmac.compare_digest(stored_token, token):
                    return False

            conn.execute(
                "UPDATE tasks SET confirmation_token = NULL WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
            return True

    def build_feedback_event(
        self,
        task_id: str,
        event_id: Optional[str] = None,
        custom_state: Optional[str] = None,
        custom_summary: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build schema/task_feedback.json compliant dictionary."""
        task = self.get_task(task_id)
        state = custom_state or (task.state if task else TaskState.RECEIVED)
        summary = custom_summary or (task.summary if task else f"Task in state {state}")
        progress = task.progress if task else {"percent": 0, "current_step": "init", "total_steps": 1, "completed_steps": 0}

        logs = [log.to_dict() for log in self.get_logs(task_id, limit=50)]
        artifacts = self.get_artifacts(task_id)
        ver_results = self.get_verification_results(task_id)

        fb: Dict[str, Any] = {
            "schema_version": "1.0.0",
            "event_id": event_id or str(uuid.uuid4()),
            "task_id": task_id,
            "timestamp": timestamp or utc_now_iso(),
            "state": state,
            "progress": progress,
            "summary": summary,
        }

        if logs:
            fb["logs"] = logs
        if artifacts:
            fb["artifacts"] = artifacts
        if ver_results:
            fb["verification_results"] = ver_results
        if task and task.error:
            fb["error"] = task.error

        return fb

    def close(self) -> None:
        """Close SQLite connection."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
