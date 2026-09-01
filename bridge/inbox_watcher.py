"""Inbox File Watcher for ChatGPT-to-Local Agent Bridge.

Monitors an inbox directory for JSON/Markdown task handoff files, performs atomic locking,
validates schemas, dispatches tasks to the bridge executor, and archives processed/failed drops.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

if __package__ is None or __package__ == "":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.schema_validator import SchemaValidationError, validate_task_handoff
from bridge.state_store import StateStore

logger = logging.getLogger("bridge.inbox_watcher")


def extract_json_from_markdown(md_content: str) -> Optional[Dict[str, Any]]:
    """Extract JSON task payload from markdown fenced code blocks."""
    # Pattern matching ```json task_handoff ... ``` or ```json ... ```
    pattern = re.compile(r"```(?:json(?:\s+task_handoff)?)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
    matches = pattern.findall(md_content)
    for m in matches:
        try:
            parsed = json.loads(m.strip())
            if isinstance(parsed, dict) and ("task" in parsed or "task_id" in parsed or "objective" in parsed):
                return parsed
        except Exception:
            continue

    # Fallback: look for any JSON block
    generic_pattern = re.compile(r"```\s*\n(\{.*?\})\s*\n```", re.DOTALL)
    generic_matches = generic_pattern.findall(md_content)
    for m in generic_matches:
        try:
            parsed = json.loads(m.strip())
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    return None


class InboxWatcher:
    """Background directory poller with atomic file locking and automatic archiving."""

    def __init__(
        self,
        inbox_dir: Union[str, Path],
        task_submit_callback: Callable[[Dict[str, Any]], Any],
        poll_interval_seconds: float = 1.0,
    ):
        self.inbox_dir = Path(inbox_dir).resolve()
        self.processed_dir = self.inbox_dir / "processed"
        self.failed_dir = self.inbox_dir / "failed"
        self.task_submit_callback = task_submit_callback
        self.poll_interval = poll_interval_seconds

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create inbox, processed, and failed directories if missing."""
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        """Start the background watcher thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ensure_directories()
        self._thread = threading.Thread(target=self._run_loop, name="InboxWatcher", daemon=True)
        self._thread.start()
        logger.info("Inbox watcher started on %s", self.inbox_dir)

    def stop(self, timeout: float = 3.0) -> None:
        """Signal watcher to stop and join thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("Inbox watcher stopped")

    def is_alive(self) -> bool:
        """Check if watcher thread is active."""
        return self._thread is not None and self._thread.is_alive()

    def _run_loop(self) -> None:
        """Main polling loop."""
        while not self._stop_event.is_set():
            try:
                self.scan_and_process_once()
            except Exception as exc:
                logger.error("Error in inbox scan loop: %s", exc, exc_info=True)

            self._stop_event.wait(timeout=self.poll_interval)

    def scan_and_process_once(self) -> List[Path]:
        """Scan directory and process pending tasks immediately. Returns list of processed files."""
        if not self.inbox_dir.exists():
            return []

        processed_files: List[Path] = []
        for entry in self.inbox_dir.iterdir():
            if self._stop_event.is_set():
                break

            if not entry.is_file():
                continue

            name = entry.name
            if name.startswith(".") or name.endswith(".lock") or name.endswith(".tmp"):
                continue

            if not (name.endswith(".json") or name.endswith(".md")):
                continue

            # Attempt atomic lock
            lock_path = entry.with_name(f".{name}.lock")
            try:
                # Open with 'x' mode (exclusive create)
                with open(lock_path, "x") as lock_file:
                    lock_file.write(f"pid={os.getpid()} ts={time.time()}\n")
            except (FileExistsError, OSError):
                # Another process holds the lock or is writing
                continue

            try:
                success, details = self._process_file(entry)
                timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
                target_filename = f"{timestamp}_{entry.name}"

                if success:
                    dest = self.processed_dir / target_filename
                    shutil.move(str(entry), str(dest))
                    processed_files.append(dest)
                    logger.info("Processed task file: %s -> %s", entry.name, dest)
                else:
                    dest = self.failed_dir / target_filename
                    shutil.move(str(entry), str(dest))
                    err_dest = self.failed_dir / f"{target_filename}.error.json"
                    with open(err_dest, "w", encoding="utf-8") as ef:
                        json.dump(
                            {
                                "source_file": entry.name,
                                "failed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                "details": details,
                            },
                            ef,
                            indent=2,
                        )
                    logger.warning("Failed task file: %s -> %s (errors recorded)", entry.name, dest)
            except Exception as exc:
                logger.error("Unexpected error processing inbox file %s: %s", entry.name, exc)
            finally:
                # Release lock
                try:
                    if lock_path.exists():
                        lock_path.unlink()
                except Exception as e:
                    logger.debug("Failed to remove lock %s: %s", lock_path, e)

        return processed_files

    def _process_file(self, file_path: Path) -> tuple[bool, Any]:
        """Parse, validate, and dispatch a single inbox task file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as read_exc:
            return False, {"error": f"Failed to read file: {read_exc}"}

        payload: Optional[Dict[str, Any]] = None
        if file_path.suffix == ".json":
            try:
                payload = json.loads(content)
            except json.JSONDecodeError as jde:
                return False, {"error": f"JSON syntax error: {jde}"}
        elif file_path.suffix == ".md":
            payload = extract_json_from_markdown(content)
            if payload is None:
                return False, {"error": "No valid JSON task_handoff block found in markdown"}

        if not isinstance(payload, dict):
            return False, {"error": "Parsed content is not a JSON object/dict"}

        # Validate schema
        is_valid, errors = validate_task_handoff(payload)
        if not is_valid:
            return False, {"schema_errors": errors}

        # Dispatch task
        try:
            self.task_submit_callback(payload)
            return True, None
        except Exception as submit_exc:
            return False, {"dispatch_error": str(submit_exc)}
