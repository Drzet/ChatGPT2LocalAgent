"""Unit and concurrency tests for the bridge StateStore.

Covers:
- SQLite database initialization (in-memory and on-disk)
- Task record creation, fetching, and full dictionary serialization
- Atomic state transitions across all 10 task lifecycle states
- Automatic timestamp capture (created_at, started_at, finished_at)
- Task filtering, querying, and pagination (limit/offset)
- Structured execution log recording and retrieval
- Artifact registration and query
- Verification result recording and retrieval
- Event history recording, since_id filtering, and feedback payload construction
- Confirmation token setting, validation, and consumption
- Thread-safety and concurrent transactions
"""

import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.state_store import LogEntry, StateStore, TaskRecord, TaskState


def get_sample_task_payload(task_id: str = "task-test-01") -> dict:
    return {
        "schema_version": "1.0.0",
        "context": {
            "session_id": "c0eebc99-9c0b-4ef8-bb6d-6bb9bd380a99",
            "source": "cli",
            "user_intent": "Test state store persistence",
            "decision_rationale": "Verify all fields and states store correctly",
            "timestamp": "2026-09-01T04:00:00Z",
        },
        "task": {
            "task_id": task_id,
            "title": "State Store Test Task",
            "description": "Comprehensive test payload for state store validation.",
            "category": "test",
            "priority": "medium",
            "complexity": "small",
        },
        "constraints": {
            "environment": {
                "os": "mac",
                "runtime": "python>=3.11",
                "cwd": "/tmp/test_workspace",
            },
            "security_policy": {
                "execution_mode": "sandboxed",
                "permitted_commands": ["python3"],
                "forbidden_commands": ["rm -rf"],
                "allowed_file_paths": ["*"],
                "forbidden_file_paths": [".env"],
            },
            "limits": {
                "max_execution_timeout_seconds": 300,
                "max_iterations": 10,
                "max_file_modifications": 5,
            },
        },
        "file_operations": [
            {
                "path": "test.txt",
                "operation": "create",
                "is_critical": True,
                "content": "hello world",
            }
        ],
        "verification": {
            "automated_checks": [
                {
                    "id": "check-01",
                    "name": "Verify file exists",
                    "type": "file_exists",
                    "target_path": "test.txt",
                    "is_blocking": True,
                }
            ],
            "acceptance_statement": "File test.txt exists with hello world",
        },
    }


class TestStateStoreBasic(unittest.TestCase):
    """Test basic CRUD, lifecycle transitions, and data persistence in StateStore."""

    def setUp(self):
        self.store = StateStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_create_and_get_task(self):
        payload = get_sample_task_payload("task-crud-1")
        record = self.store.create_task(payload, initial_state=TaskState.RECEIVED)
        self.assertIsNotNone(record)
        self.assertEqual(record.task_id, "task-crud-1")
        self.assertEqual(record.state, TaskState.RECEIVED)
        self.assertEqual(record.title, "State Store Test Task")
        self.assertEqual(record.workspace_root, "/tmp/test_workspace")
        self.assertEqual(record.execution_mode, "sandboxed")
        self.assertIsNotNone(record.created_at)

        # Retrieve task
        fetched = self.store.get_task("task-crud-1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.task_id, "task-crud-1")
        self.assertEqual(fetched.payload["task"]["title"], "State Store Test Task")

        # Serialized dict check
        d = fetched.to_dict()
        self.assertEqual(d["task_id"], "task-crud-1")
        self.assertEqual(d["state"], TaskState.RECEIVED)

    def test_get_nonexistent_task(self):
        self.assertIsNone(self.store.get_task("nonexistent-task-id"))

    def test_update_task_state_all_lifecycle_states(self):
        payload = get_sample_task_payload("task-states-1")
        self.store.create_task(payload, initial_state=TaskState.RECEIVED)

        all_states = [
            TaskState.VALIDATING,
            TaskState.QUEUED,
            TaskState.RUNNING,
            TaskState.WAITING_CONFIRMATION,
            TaskState.WAITING_USER_INPUT,
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.BLOCKED,
        ]

        for state in all_states:
            rec = self.store.update_task_state(
                task_id="task-states-1",
                state=state,
                summary=f"Switched to state {state}",
                progress={"percent": 50, "current_step": f"step_{state}"},
            )
            self.assertIsNotNone(rec)
            self.assertEqual(rec.state, state)
            self.assertEqual(rec.summary, f"Switched to state {state}")
            self.assertEqual(rec.progress["percent"], 50)

    def test_timestamps_lifecycle(self):
        payload = get_sample_task_payload("task-ts-1")
        self.store.create_task(payload, initial_state=TaskState.QUEUED)

        rec = self.store.get_task("task-ts-1")
        self.assertIsNone(rec.started_at)
        self.assertIsNone(rec.finished_at)

        # Transition to RUNNING sets started_at
        self.store.update_task_state("task-ts-1", TaskState.RUNNING)
        rec_running = self.store.get_task("task-ts-1")
        self.assertIsNotNone(rec_running.started_at)
        self.assertIsNone(rec_running.finished_at)

        # Transition to SUCCEEDED sets finished_at
        self.store.update_task_state("task-ts-1", TaskState.SUCCEEDED)
        rec_finished = self.store.get_task("task-ts-1")
        self.assertIsNotNone(rec_finished.finished_at)

    def test_list_tasks_and_filters(self):
        for i in range(10):
            payload = get_sample_task_payload(f"task-list-{i}")
            state = TaskState.SUCCEEDED if i % 2 == 0 else TaskState.FAILED
            self.store.create_task(payload, initial_state=state)

        # All tasks
        all_tasks = self.store.list_tasks(limit=20)
        self.assertEqual(len(all_tasks), 10)

        # Filter by state
        succeeded = self.store.list_tasks(state=TaskState.SUCCEEDED)
        self.assertEqual(len(succeeded), 5)
        failed = self.store.list_tasks(state=TaskState.FAILED)
        self.assertEqual(len(failed), 5)

        # Pagination
        p1 = self.store.list_tasks(limit=3, offset=0)
        p2 = self.store.list_tasks(limit=3, offset=3)
        self.assertEqual(len(p1), 3)
        self.assertEqual(len(p2), 3)
        self.assertNotEqual(p1[0].task_id, p2[0].task_id)

    def test_structured_logs(self):
        task_id = "task-log-test"
        self.store.create_task(get_sample_task_payload(task_id))

        self.store.append_log(task_id, "Log message 1", level="info", source="agent")
        self.store.append_log(
            task_id,
            "Log message 2 with metadata",
            level="warn",
            source="verifier",
            metadata={"check_id": "c1", "duration": 12},
        )
        self.store.append_log(task_id, "Error log", level="error", source="system")

        logs = self.store.get_logs(task_id)
        self.assertEqual(len(logs), 3)
        self.assertEqual(logs[0].message, "Log message 1")
        self.assertEqual(logs[0].level, "info")
        self.assertEqual(logs[1].metadata, {"check_id": "c1", "duration": 12})
        self.assertEqual(logs[2].level, "error")

        # Serialized dict check
        d = logs[1].to_dict()
        self.assertEqual(d["level"], "warn")
        self.assertIn("metadata", d)

    def test_artifacts_tracking(self):
        task_id = "task-artifact-test"
        self.store.create_task(get_sample_task_payload(task_id))

        art1 = self.store.record_artifact(
            task_id=task_id,
            path="src/main.py",
            artifact_type="source_code",
            size_bytes=2048,
            sha256="d" * 64,
            description="Created main.py",
        )
        self.assertEqual(art1["path"], "src/main.py")

        art2 = self.store.record_artifact(
            task_id=task_id,
            path="report.txt",
            artifact_type="test_report",
            size_bytes=512,
        )

        artifacts = self.store.get_artifacts(task_id)
        self.assertEqual(len(artifacts), 2)
        self.assertEqual(artifacts[0]["path"], "src/main.py")
        self.assertEqual(artifacts[0]["sha256"], "d" * 64)
        self.assertEqual(artifacts[1]["type"], "test_report")

    def test_verification_results_tracking(self):
        task_id = "task-vr-test"
        self.store.create_task(get_sample_task_payload(task_id))

        self.store.record_verification_result(
            task_id=task_id,
            step_id="step-1",
            name="Verify unit tests pass",
            status="passed",
            duration_ms=120,
            exit_code=0,
            stdout_snippet="OK (3 tests passed)",
        )
        self.store.record_verification_result(
            task_id=task_id,
            step_id="step-2",
            name="Verify build binary",
            status="failed",
            duration_ms=450,
            exit_code=1,
            stderr_snippet="Syntax error in main.go:12",
            failure_reason="Non-zero exit code 1",
        )

        v_results = self.store.get_verification_results(task_id)
        self.assertEqual(len(v_results), 2)
        self.assertEqual(v_results[0]["status"], "passed")
        self.assertEqual(v_results[0]["exit_code"], 0)
        self.assertEqual(v_results[1]["status"], "failed")
        self.assertEqual(v_results[1]["failure_reason"], "Non-zero exit code 1")

    def test_events_and_feedback_building(self):
        task_id = "task-event-test"
        self.store.create_task(get_sample_task_payload(task_id), initial_state=TaskState.RECEIVED)

        self.store.update_task_state(task_id, TaskState.RUNNING, summary="Executing step 1")
        self.store.append_log(task_id, "Building files...")
        self.store.record_artifact(task_id, "out.bin", "binary", 1024)

        feedback = self.store.build_feedback_event(task_id)
        self.assertEqual(feedback["schema_version"], "1.0.0")
        self.assertEqual(feedback["task_id"], task_id)
        self.assertEqual(feedback["state"], TaskState.RUNNING)
        self.assertEqual(len(feedback["logs"]), 1)
        self.assertEqual(len(feedback["artifacts"]), 1)

        events = self.store.get_events(task_id)
        self.assertGreaterEqual(len(events), 2)  # Initial RECEIVED + RUNNING

        # Test since_id pagination
        first_id = events[0]["_db_id"]
        later_events = self.store.get_events(task_id, since_id=first_id)
        self.assertEqual(len(later_events), len(events) - 1)

    def test_confirmation_token_validation(self):
        task_id = "task-confirm-test"
        self.store.create_task(get_sample_task_payload(task_id), initial_state=TaskState.WAITING_CONFIRMATION)

        self.store.set_confirmation_token(task_id, "approval-token-xyz")

        # Wrong state -> fails
        self.store.update_task_state(task_id, TaskState.RUNNING)
        self.assertFalse(self.store.verify_and_consume_confirmation(task_id, "approval-token-xyz"))

        # In WAITING_CONFIRMATION with wrong token -> fails
        self.store.update_task_state(task_id, TaskState.WAITING_CONFIRMATION)
        self.store.set_confirmation_token(task_id, "approval-token-xyz")
        self.assertFalse(self.store.verify_and_consume_confirmation(task_id, "wrong-token"))

        # In WAITING_CONFIRMATION with correct token -> succeeds and consumes token
        self.assertTrue(self.store.verify_and_consume_confirmation(task_id, "approval-token-xyz"))

        # Move to RUNNING after confirmation
        self.store.update_task_state(task_id, TaskState.RUNNING)

        # Subsequent attempt fails because task is now RUNNING
        self.assertFalse(self.store.verify_and_consume_confirmation(task_id, "approval-token-xyz"))


class TestStateStoreDiskAndConcurrency(unittest.TestCase):
    """Test on-disk SQLite persistence, WAL mode, and concurrent multi-threaded writes."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tasks.db"
        self.store = StateStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_disk_persistence_across_reopen(self):
        task_id = "task-persist-1"
        self.store.create_task(get_sample_task_payload(task_id), initial_state=TaskState.QUEUED)
        self.store.append_log(task_id, "Persisted log message")
        self.store.close()

        # Reopen same database file
        store2 = StateStore(self.db_path)
        fetched = store2.get_task(task_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.state, TaskState.QUEUED)

        logs = store2.get_logs(task_id)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].message, "Persisted log message")
        store2.close()

    def test_concurrent_writes(self):
        task_id = "task-concurrent-1"
        self.store.create_task(get_sample_task_payload(task_id))

        def worker_log(thread_idx: int):
            for i in range(20):
                self.store.append_log(task_id, f"Thread {thread_idx} log {i}")

        threads = [threading.Thread(target=worker_log, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        logs = self.store.get_logs(task_id, limit=200)
        self.assertEqual(len(logs), 100)


if __name__ == "__main__":
    unittest.main()
