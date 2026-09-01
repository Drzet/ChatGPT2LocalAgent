"""Unit and integration tests for the InboxWatcher and markdown payload parser.

Covers:
- Markdown fenced JSON block extraction (extract_json_from_markdown)
- Directory structure initialization (inbox, processed, failed)
- Scanning, atomic locking (.lock file creation/release), and processing valid JSON drops
- Processing valid markdown task files
- Handling malformed JSON and schema failures with archiving to inbox/failed/ and error diagnostic files
- Handling file lock contention
- Background thread lifecycle (start, stop, is_alive)
- Callback error handling and failure archiving
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.inbox_watcher import InboxWatcher, extract_json_from_markdown


def get_valid_handoff_dict(task_id: str = "inbox-task-01") -> dict:
    return {
        "schema_version": "1.0.0",
        "context": {
            "session_id": "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380b11",
            "source": "file_sync",
            "user_intent": "Inbox watcher integration test",
            "decision_rationale": "Verify inbox scanning and archiving logic",
            "timestamp": "2026-09-01T04:00:00Z",
        },
        "task": {
            "task_id": task_id,
            "title": "Inbox Task",
            "description": "Task dropped into inbox folder.",
            "category": "feature",
            "priority": "medium",
            "complexity": "small",
        },
        "constraints": {
            "environment": {"os": "mac", "runtime": "python>=3.11", "cwd": "."},
            "security_policy": {
                "execution_mode": "sandboxed",
                "permitted_commands": ["python3"],
                "forbidden_commands": ["rm -rf /"],
                "allowed_file_paths": ["*"],
                "forbidden_file_paths": [],
            },
            "limits": {
                "max_execution_timeout_seconds": 300,
                "max_iterations": 10,
                "max_file_modifications": 5,
            },
        },
        "file_operations": [],
        "verification": {
            "automated_checks": [],
            "acceptance_statement": "Inbox task processed successfully",
        },
    }


class TestMarkdownExtraction(unittest.TestCase):
    """Test extracting JSON task handoff payloads from Markdown text."""

    def test_extract_json_fenced_block_with_tag(self):
        payload = get_valid_handoff_dict("task-md-tag")
        md = f"""# Design Decision
Here is the task for local execution:

```json task_handoff
{json.dumps(payload, indent=2)}
```

Please run this.
"""
        extracted = extract_json_from_markdown(md)
        self.assertIsNotNone(extracted)
        self.assertEqual(extracted["task"]["task_id"], "task-md-tag")

    def test_extract_json_fenced_block_generic(self):
        payload = get_valid_handoff_dict("task-md-generic")
        md = f"""## Context
```json
{json.dumps(payload, indent=2)}
```
"""
        extracted = extract_json_from_markdown(md)
        self.assertIsNotNone(extracted)
        self.assertEqual(extracted["task"]["task_id"], "task-md-generic")

    def test_extract_from_plain_markdown_returns_none(self):
        md = "# No json block here\nJust text discussion."
        self.assertIsNone(extract_json_from_markdown(md))

    def test_extract_from_invalid_json_block_returns_none(self):
        md = "```json\n{ not valid json\n```"
        self.assertIsNone(extract_json_from_markdown(md))


class TestInboxWatcher(unittest.TestCase):
    """Test InboxWatcher directory polling, atomic locking, and archiving."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.inbox_dir = Path(self.temp_dir.name).resolve() / "inbox"
        self.submitted_tasks = []

        def on_submit(payload):
            self.submitted_tasks.append(payload)

        self.watcher = InboxWatcher(
            inbox_dir=self.inbox_dir,
            task_submit_callback=on_submit,
            poll_interval_seconds=0.1,
        )

    def tearDown(self):
        self.watcher.stop()
        self.temp_dir.cleanup()

    def test_directory_creation(self):
        self.assertTrue(self.inbox_dir.exists())
        self.assertTrue((self.inbox_dir / "processed").exists())
        self.assertTrue((self.inbox_dir / "failed").exists())

    def test_process_valid_json_file(self):
        payload = get_valid_handoff_dict("inbox-json-01")
        drop_file = self.inbox_dir / "task_01.json"
        drop_file.write_text(json.dumps(payload))

        processed = self.watcher.scan_and_process_once()
        self.assertEqual(len(processed), 1)
        self.assertEqual(len(self.submitted_tasks), 1)
        self.assertEqual(self.submitted_tasks[0]["task"]["task_id"], "inbox-json-01")

        # Original drop file moved to processed
        self.assertFalse(drop_file.exists())
        processed_files = list((self.inbox_dir / "processed").glob("*task_01.json"))
        self.assertEqual(len(processed_files), 1)

    def test_process_valid_markdown_file(self):
        payload = get_valid_handoff_dict("inbox-md-01")
        md_text = f"# Decision\n```json task_handoff\n{json.dumps(payload)}\n```"
        drop_file = self.inbox_dir / "task_handoff.md"
        drop_file.write_text(md_text)

        processed = self.watcher.scan_and_process_once()
        self.assertEqual(len(processed), 1)
        self.assertEqual(len(self.submitted_tasks), 1)
        self.assertEqual(self.submitted_tasks[0]["task"]["task_id"], "inbox-md-01")

        self.assertFalse(drop_file.exists())
        processed_files = list((self.inbox_dir / "processed").glob("*task_handoff.md"))
        self.assertEqual(len(processed_files), 1)

    def test_process_invalid_json_syntax(self):
        drop_file = self.inbox_dir / "corrupt.json"
        drop_file.write_text("{ incomplete json...")

        processed = self.watcher.scan_and_process_once()
        self.assertEqual(len(processed), 0)
        self.assertEqual(len(self.submitted_tasks), 0)

        # Original file moved to failed
        self.assertFalse(drop_file.exists())
        failed_files = list((self.inbox_dir / "failed").glob("*corrupt.json"))
        self.assertEqual(len(failed_files), 1)

        # Error diagnostic file created
        err_files = list((self.inbox_dir / "failed").glob("*corrupt.json.error.json"))
        self.assertEqual(len(err_files), 1)
        err_data = json.loads(err_files[0].read_text())
        self.assertIn("JSON syntax error", err_data["details"]["error"])

    def test_process_invalid_schema_payload(self):
        drop_file = self.inbox_dir / "bad_schema.json"
        drop_file.write_text(json.dumps({"invalid": "schema"}))

        processed = self.watcher.scan_and_process_once()
        self.assertEqual(len(processed), 0)

        failed_files = list((self.inbox_dir / "failed").glob("*bad_schema.json"))
        self.assertEqual(len(failed_files), 1)

        err_files = list((self.inbox_dir / "failed").glob("*bad_schema.json.error.json"))
        self.assertEqual(len(err_files), 1)
        err_data = json.loads(err_files[0].read_text())
        self.assertIn("schema_errors", err_data["details"])

    def test_lock_contention_skips_file(self):
        payload = get_valid_handoff_dict("inbox-locked")
        drop_file = self.inbox_dir / "locked_task.json"
        drop_file.write_text(json.dumps(payload))

        # Manually create lock file
        lock_file = self.inbox_dir / ".locked_task.json.lock"
        lock_file.write_text("pid=99999\n")

        # Scan should skip locked file
        processed = self.watcher.scan_and_process_once()
        self.assertEqual(len(processed), 0)
        self.assertTrue(drop_file.exists())

        # Clean up lock
        lock_file.unlink()

        # Now scan processes file
        processed_after = self.watcher.scan_and_process_once()
        self.assertEqual(len(processed_after), 1)
        self.assertFalse(drop_file.exists())

    def test_ignore_temporary_and_hidden_files(self):
        (self.inbox_dir / ".hidden.json").write_text("{}")
        (self.inbox_dir / "draft.tmp").write_text("{}")
        (self.inbox_dir / "readme.txt").write_text("plain text")

        processed = self.watcher.scan_and_process_once()
        self.assertEqual(len(processed), 0)
        self.assertEqual(len(self.submitted_tasks), 0)

    def test_background_watcher_thread_lifecycle(self):
        self.assertFalse(self.watcher.is_alive())
        self.watcher.start()
        self.assertTrue(self.watcher.is_alive())

        # Drop file while thread is running
        payload = get_valid_handoff_dict("bg-task-01")
        drop_file = self.inbox_dir / "bg_task.json"
        drop_file.write_text(json.dumps(payload))

        # Wait for background poller to process
        start = time.time()
        while time.time() - start < 3.0:
            if len(self.submitted_tasks) > 0:
                break
            time.sleep(0.05)

        self.assertEqual(len(self.submitted_tasks), 1)
        self.assertEqual(self.submitted_tasks[0]["task"]["task_id"], "bg-task-01")

        self.watcher.stop()
        self.assertFalse(self.watcher.is_alive())

    def test_generic_markdown_block_extraction(self):
        md = "Some text\n```\n{\"task\": {\"task_id\": \"task-gen\"}}\n```\n"
        res = extract_json_from_markdown(md)
        self.assertIsNotNone(res)
        self.assertEqual(res["task"]["task_id"], "task-gen")

        # Invalid json block
        self.assertIsNone(extract_json_from_markdown("```\n{not json\n```"))

        # No block
        self.assertIsNone(extract_json_from_markdown("plain text without code blocks"))

    def test_start_already_running_and_stop_not_running(self):
        self.watcher.start()
        self.assertTrue(self.watcher.is_alive())
        # Starting again should be a no-op
        self.watcher.start()
        self.watcher.stop()
        self.assertFalse(self.watcher.is_alive())
        # Stopping again should be a no-op
        self.watcher.stop()

    def test_scan_nonexistent_directory(self):
        w = InboxWatcher(self.inbox_dir / "does_not_exist", task_submit_callback=lambda x: None)
        shutil.rmtree(w.inbox_dir)
        res = w.scan_and_process_once()
        self.assertEqual(res, [])

    def test_invalid_payload_types_and_dispatch_failure(self):
        # 1. Markdown without valid json
        (self.inbox_dir / "no_json.md").write_text("# Just Markdown\nNo json here.")
        # 2. Non-dict JSON (e.g. array)
        (self.inbox_dir / "array.json").write_text("[1, 2, 3]")

        processed = self.watcher.scan_and_process_once()
        self.assertEqual(len(processed), 0)

        failed_files = list(self.watcher.failed_dir.glob("*.json"))
        self.assertGreaterEqual(len(failed_files), 2)

        # 3. Callback exception
        def failing_callback(p):
            raise RuntimeError("DB locked during dispatch")

        w_fail = InboxWatcher(self.inbox_dir, task_submit_callback=failing_callback)
        (self.inbox_dir / "valid_for_fail.json").write_text(json.dumps(get_valid_handoff_dict("task-disp-fail")))
        w_fail.scan_and_process_once()
        err_files = list(w_fail.failed_dir.glob("*valid_for_fail.json.error.json"))
        self.assertEqual(len(err_files), 1)


if __name__ == "__main__":
    unittest.main()
