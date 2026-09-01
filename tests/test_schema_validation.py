"""Comprehensive unit and boundary tests for schema validation.

Covers:
- Task Handoff schema validation (schema/task_handoff.json)
- Task Feedback schema validation (schema/task_feedback.json)
- Mandatory field enforcement and unexpected property rejection
- Format and regex checks (UUID, SemVer, ISO datetime, SHA256, Task IDs)
- String length constraints and integer ranges
- Enum value validation across all sub-schemas
- Parsing and exception handling
"""

import copy
import json
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.schema_validator import (
    SchemaValidationError,
    parse_and_validate_task_handoff,
    validate_task_feedback,
    validate_task_handoff,
)


def get_valid_task_handoff_payload() -> dict:
    """Return a baseline 100% valid Task Handoff payload."""
    return {
        "schema_version": "1.0.0",
        "context": {
            "session_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
            "conversation_id": "conv-12345",
            "message_id": "msg-67890",
            "timestamp": "2026-09-01T04:00:00Z",
            "source": "chatgpt_web_action",
            "user_intent": "Implement healthcheck route in backend server",
            "decision_rationale": "Decided to use standard /health endpoint with JSON status output",
            "discussion_summary": "Discussed endpoints and agreed on lightweight GET /health probe.",
            "discussion_history": [
                {
                    "role": "user",
                    "content": "Can we add a healthcheck endpoint?",
                    "timestamp": "2026-09-01T03:59:00Z",
                },
                {
                    "role": "assistant",
                    "content": "Yes, I will create a handoff task for the local agent.",
                    "timestamp": "2026-09-01T03:59:30Z",
                },
            ],
            "branch_or_context": "feature/healthcheck",
            "custom_metadata": {"author": "developer", "environment": "staging"},
        },
        "task": {
            "task_id": "task-healthcheck-01",
            "title": "Add GET /health Endpoint",
            "description": "Implement a lightweight health check endpoint in server.py returning {status: ok}.",
            "category": "feature",
            "priority": "high",
            "complexity": "small",
            "tags": ["backend", "api", "monitoring"],
            "dependencies": ["task-init-00"],
            "estimated_duration_seconds": 120,
        },
        "constraints": {
            "environment": {
                "os": "mac",
                "runtime": "python>=3.11",
                "cwd": "/Users/mengma/project",
                "env_vars": {"PORT": "8080", "ENV": "test"},
            },
            "security_policy": {
                "execution_mode": "sandboxed",
                "permitted_commands": ["^python3", "^pytest"],
                "forbidden_commands": [r"\brm\s+-rf\b", r"\bsudo\b"],
                "allowed_file_paths": ["src/**", "tests/**"],
                "forbidden_file_paths": [".env", "id_rsa*"],
                "network_access": "local_only",
            },
            "limits": {
                "max_execution_timeout_seconds": 300,
                "max_iterations": 20,
                "max_file_modifications": 10,
                "max_token_budget": 50000,
            },
            "deterministic_parameters": {
                "temperature": 0.0,
                "seed": 42,
                "strict_mode": True,
            },
        },
        "file_operations": [
            {
                "path": "src/server.py",
                "operation": "modify",
                "is_critical": True,
                "description": "Register /health route handler",
                "line_ranges": [{"start_line": 15, "end_line": 25}],
                "content": "# Health route\ndef health():\n    return {'status': 'ok'}\n",
                "expected_sha256_before": "a" * 64,
                "expected_sha256_after": "b" * 64,
            }
        ],
        "verification": {
            "automated_checks": [
                {
                    "id": "check-health-route",
                    "name": "Verify health endpoint test passes",
                    "type": "command",
                    "command": "python3 -m unittest tests/test_server.py",
                    "expected_exit_code": 0,
                    "expected_stdout_contains": ["OK"],
                    "timeout_seconds": 30,
                    "is_blocking": True,
                }
            ],
            "manual_verification_steps": ["Run curl http://127.0.0.1:8080/health"],
            "acceptance_statement": "Server responds with HTTP 200 and {'status': 'ok'}",
        },
        "execution_options": {
            "auto_rollback_on_failure": True,
            "notify_channel": "websocket",
            "create_git_branch": "feature/healthcheck",
        },
        "extensions": {"custom_flag": True},
    }


def get_valid_task_feedback_payload() -> dict:
    """Return a baseline 100% valid Task Feedback payload."""
    return {
        "schema_version": "1.0.0",
        "event_id": "b1ffbc99-9c0b-4ef8-bb6d-6bb9bd380b22",
        "task_id": "task-healthcheck-01",
        "timestamp": "2026-09-01T04:05:00Z",
        "state": "running",
        "progress": {
            "percent": 50,
            "current_step": "Modifying src/server.py",
            "total_steps": 4,
            "completed_steps": 2,
            "estimated_remaining_seconds": 60,
        },
        "summary": "Modified server.py, proceeding to run automated checks",
        "logs": [
            {
                "timestamp": "2026-09-01T04:04:50Z",
                "level": "info",
                "source": "agent",
                "message": "Writing updated route definitions",
                "metadata": {"lines_added": 12},
            }
        ],
        "artifacts": [
            {
                "path": "src/server.py",
                "type": "source_code",
                "size_bytes": 1024,
                "sha256": "c" * 64,
                "description": "Updated server.py with /health endpoint",
            }
        ],
        "verification_results": [
            {
                "step_id": "check-health-route",
                "name": "Verify health endpoint test passes",
                "status": "passed",
                "exit_code": 0,
                "duration_ms": 450,
                "stdout_snippet": "Ran 1 test in 0.05s\nOK",
            }
        ],
        "error": None,
        "user_prompt": None,
        "custom_data": {"telemetry_version": "1.2"},
    }


class TestTaskHandoffSchemaValidation(unittest.TestCase):
    """Test validation of task handoff payloads against schema/task_handoff.json."""

    def test_valid_payload_passes(self):
        payload = get_valid_task_handoff_payload()
        is_valid, errors = validate_task_handoff(payload)
        self.assertTrue(is_valid, f"Validation failed with errors: {errors}")
        self.assertEqual(len(errors), 0)

    def test_non_dict_payload_fails(self):
        is_valid, errors = validate_task_handoff("not a dict")
        self.assertFalse(is_valid)
        self.assertTrue(any("Expected object" in e for e in errors))

    def test_missing_top_level_required_fields(self):
        for field in ["schema_version", "context", "task", "constraints", "file_operations", "verification"]:
            payload = get_valid_task_handoff_payload()
            del payload[field]
            is_valid, errors = validate_task_handoff(payload)
            self.assertFalse(is_valid)
            self.assertTrue(any(f"Missing required top-level field: '{field}'" in e for e in errors))

    def test_unexpected_top_level_property(self):
        payload = get_valid_task_handoff_payload()
        payload["unknown_evil_field"] = "malicious_data"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)
        self.assertTrue(any("Unexpected property" in e for e in errors))

    def test_invalid_schema_version_format(self):
        payload = get_valid_task_handoff_payload()
        payload["schema_version"] = "v1.0"  # Needs to match semver ^[0-9]+\.[0-9]+\.[0-9]+$
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

        payload["schema_version"] = 1.0  # Wrong type
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

    def test_context_session_id_uuid_validation(self):
        payload = get_valid_task_handoff_payload()
        payload["context"]["session_id"] = "not-a-valid-uuid"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)
        self.assertTrue(any("session_id" in e for e in errors))

    def test_context_source_enums(self):
        valid_sources = [
            "chatgpt_web_action",
            "browser_extension",
            "clipboard_sync",
            "mcp_gateway",
            "file_sync",
            "cli",
            "manual",
        ]
        for src in valid_sources:
            payload = get_valid_task_handoff_payload()
            payload["context"]["source"] = src
            is_valid, errors = validate_task_handoff(payload)
            self.assertTrue(is_valid, f"Source '{src}' should be valid")

        payload = get_valid_task_handoff_payload()
        payload["context"]["source"] = "invalid_satellite_source"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

    def test_context_string_lengths(self):
        # user_intent: min 3, max 1000
        payload = get_valid_task_handoff_payload()
        payload["context"]["user_intent"] = "hi"  # < 3 chars
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

        payload["context"]["user_intent"] = "x" * 1001  # > 1000 chars
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

        # decision_rationale: min 5
        payload = get_valid_task_handoff_payload()
        payload["context"]["decision_rationale"] = "nope"  # 4 chars < 5
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

    def test_context_discussion_history_validation(self):
        payload = get_valid_task_handoff_payload()
        # Invalid role
        payload["context"]["discussion_history"] = [
            {"role": "bad_role", "content": "hello"}
        ]
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

        # Missing content
        payload["context"]["discussion_history"] = [
            {"role": "user"}
        ]
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

    def test_task_id_pattern(self):
        payload = get_valid_task_handoff_payload()
        payload["task"]["task_id"] = "ab"  # < 3 chars
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

        payload["task"]["task_id"] = "task with spaces"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

        payload["task"]["task_id"] = "valid-task_id-123"
        is_valid, errors = validate_task_handoff(payload)
        self.assertTrue(is_valid)

    def test_task_category_priority_complexity_enums(self):
        payload = get_valid_task_handoff_payload()
        payload["task"]["category"] = "invalid_category"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

        payload = get_valid_task_handoff_payload()
        payload["task"]["priority"] = "ultra_emergency"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

        payload = get_valid_task_handoff_payload()
        payload["task"]["complexity"] = "galactic"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

    def test_constraints_environment_os_enum(self):
        for valid_os in ["mac", "linux", "windows", "any"]:
            payload = get_valid_task_handoff_payload()
            payload["constraints"]["environment"]["os"] = valid_os
            is_valid, _ = validate_task_handoff(payload)
            self.assertTrue(is_valid)

        payload = get_valid_task_handoff_payload()
        payload["constraints"]["environment"]["os"] = "solaris"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

    def test_constraints_limits_ranges(self):
        # max_execution_timeout_seconds: 1 to 86400
        payload = get_valid_task_handoff_payload()
        payload["constraints"]["limits"]["max_execution_timeout_seconds"] = 0
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

        payload["constraints"]["limits"]["max_execution_timeout_seconds"] = 86401
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

        # max_iterations: 1 to 1000
        payload = get_valid_task_handoff_payload()
        payload["constraints"]["limits"]["max_iterations"] = 1001
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

    def test_file_operations_operation_enum(self):
        valid_ops = ["create", "modify", "delete", "inspect", "rename", "patch"]
        for op in valid_ops:
            payload = get_valid_task_handoff_payload()
            payload["file_operations"][0]["operation"] = op
            is_valid, _ = validate_task_handoff(payload)
            self.assertTrue(is_valid)

        payload = get_valid_task_handoff_payload()
        payload["file_operations"][0]["operation"] = "destroy"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

    def test_file_operations_sha256_format(self):
        payload = get_valid_task_handoff_payload()
        payload["file_operations"][0]["expected_sha256_before"] = "not-64-hex-chars"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

    def test_verification_automated_checks_type_enum(self):
        valid_types = ["command", "file_exists", "file_contains", "file_matches_regex", "http_check", "test_suite"]
        for vt in valid_types:
            payload = get_valid_task_handoff_payload()
            payload["verification"]["automated_checks"][0]["type"] = vt
            is_valid, _ = validate_task_handoff(payload)
            self.assertTrue(is_valid)

        payload = get_valid_task_handoff_payload()
        payload["verification"]["automated_checks"][0]["type"] = "smoke_test"
        is_valid, errors = validate_task_handoff(payload)
        self.assertFalse(is_valid)

    def test_parse_and_validate_task_handoff(self):
        payload = get_valid_task_handoff_payload()
        json_str = json.dumps(payload)

        # Parsing string
        parsed = parse_and_validate_task_handoff(json_str)
        self.assertEqual(parsed["task"]["task_id"], payload["task"]["task_id"])

        # Parsing dict directly
        parsed_dict = parse_and_validate_task_handoff(payload)
        self.assertEqual(parsed_dict["schema_version"], "1.0.0")

        # Invalid JSON syntax
        with self.assertRaises(SchemaValidationError):
            parse_and_validate_task_handoff("invalid { json [ syntax")

        # Invalid data types
        with self.assertRaises(SchemaValidationError):
            parse_and_validate_task_handoff(12345)


class TestTaskFeedbackSchemaValidation(unittest.TestCase):
    """Test validation of task feedback payloads against schema/task_feedback.json."""

    def test_valid_feedback_passes(self):
        payload = get_valid_task_feedback_payload()
        is_valid, errors = validate_task_feedback(payload)
        self.assertTrue(is_valid, f"Validation failed: {errors}")
        self.assertEqual(len(errors), 0)

    def test_missing_feedback_required_fields(self):
        for field in ["schema_version", "event_id", "task_id", "timestamp", "state", "progress", "summary"]:
            payload = get_valid_task_feedback_payload()
            del payload[field]
            is_valid, errors = validate_task_feedback(payload)
            self.assertFalse(is_valid)
            self.assertTrue(any(f"Missing required top-level field: '{field}'" in e for e in errors))

    def test_feedback_state_enums(self):
        all_states = [
            "received",
            "validating",
            "queued",
            "running",
            "waiting_confirmation",
            "waiting_user_input",
            "succeeded",
            "failed",
            "cancelled",
            "blocked",
        ]
        for st in all_states:
            payload = get_valid_task_feedback_payload()
            payload["state"] = st
            is_valid, _ = validate_task_feedback(payload)
            self.assertTrue(is_valid, f"State '{st}' should be valid")

        payload = get_valid_task_feedback_payload()
        payload["state"] = "unknown_state"
        is_valid, errors = validate_task_feedback(payload)
        self.assertFalse(is_valid)

    def test_feedback_progress_bounds(self):
        # Progress percent 0-100
        payload = get_valid_task_feedback_payload()
        payload["progress"]["percent"] = -1
        is_valid, errors = validate_task_feedback(payload)
        self.assertFalse(is_valid)

        payload["progress"]["percent"] = 101
        is_valid, errors = validate_task_feedback(payload)
        self.assertFalse(is_valid)

        payload["progress"]["percent"] = 0
        is_valid, _ = validate_task_feedback(payload)
        self.assertTrue(is_valid)

        payload["progress"]["percent"] = 100
        is_valid, _ = validate_task_feedback(payload)
        self.assertTrue(is_valid)

    def test_feedback_log_levels_and_sources(self):
        for lvl in ["debug", "info", "warn", "error"]:
            payload = get_valid_task_feedback_payload()
            payload["logs"][0]["level"] = lvl
            is_valid, _ = validate_task_feedback(payload)
            self.assertTrue(is_valid)

        for src in ["agent", "stdout", "stderr", "system", "verifier"]:
            payload = get_valid_task_feedback_payload()
            payload["logs"][0]["source"] = src
            is_valid, _ = validate_task_feedback(payload)
            self.assertTrue(is_valid)

    def test_feedback_error_codes(self):
        valid_error_codes = [
            "ERR_SCHEMA_VALIDATION",
            "ERR_SECURITY_VIOLATION",
            "ERR_FORBIDDEN_PATH",
            "ERR_FORBIDDEN_COMMAND",
            "ERR_TIMEOUT",
            "ERR_VERIFICATION_FAILED",
            "ERR_DEPENDENCY_MISSING",
            "ERR_AGENT_CRASH",
            "ERR_UNAUTHORIZED",
            "ERR_FILE_CONFLICT",
            "ERR_USER_CANCELLED",
            "ERR_INTERNAL",
        ]
        for err_code in valid_error_codes:
            payload = get_valid_task_feedback_payload()
            payload["error"] = {
                "code": err_code,
                "message": f"Error simulation for {err_code}",
                "is_fatal": True,
                "stack_trace": "Traceback...",
                "remediation_suggestion": "Check config",
            }
    def test_feedback_missing_and_unexpected_fields(self):
        # Non dict
        is_valid, errs = validate_task_feedback("not a dict")
        self.assertFalse(is_valid)

        # Missing required
        for req in ["schema_version", "event_id", "task_id", "timestamp", "state", "summary", "progress"]:
            p = get_valid_task_feedback_payload()
            del p[req]
            is_valid, errs = validate_task_feedback(p)
            self.assertFalse(is_valid)

        # Unexpected property
        p2 = get_valid_task_feedback_payload()
        p2["unknown_feedback_field"] = "bad"
        is_valid, errs = validate_task_feedback(p2)
        self.assertFalse(is_valid)

        # Invalid state enum
        p3 = get_valid_task_feedback_payload()
        p3["state"] = "unknown_state_xyz"
        is_valid, errs = validate_task_feedback(p3)
        self.assertFalse(is_valid)

        # Invalid error code
        p4 = get_valid_task_feedback_payload()
        p4["error"] = {"code": "BAD_CODE", "message": "bad", "is_fatal": True}
        is_valid, errs = validate_task_feedback(p4)
        self.assertFalse(is_valid)

        # Invalid log entry
        p5 = get_valid_task_feedback_payload()
        p5["logs"] = [{"timestamp": "bad-date", "level": "info", "source": "agent", "message": "m"}]
        is_valid, errs = validate_task_feedback(p5)
        self.assertFalse(is_valid)

    def test_parse_and_validate_task_handoff_invalid_json(self):
        with self.assertRaises(SchemaValidationError):
            parse_and_validate_task_handoff("{not-valid-json")

        with self.assertRaises(SchemaValidationError):
            parse_and_validate_task_handoff("[1, 2, 3]")

    def test_handoff_execution_options_and_extensions(self):
        payload = get_valid_task_handoff_payload()
        payload["execution_options"] = {
            "auto_rollback_on_failure": True,
            "notify_channel": "websocket",
            "create_git_branch": "feat/new-auth",
        }
        payload["extensions"] = {
            "custom_metadata": {"key": "value"},
            "plugin_settings": {"mode": "fast"},
        }
        is_valid, errors = validate_task_handoff(payload)
        self.assertTrue(is_valid, f"Expected valid payload with extensions, got: {errors}")

    def test_feedback_artifacts_and_verification_results(self):
        payload = get_valid_task_feedback_payload()
        payload["artifacts"] = [
            {
                "path": "src/auth.py",
                "type": "source_code",
                "size_bytes": 1024,
                "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "description": "Auth module",
            }
        ]
        payload["verification_results"] = [
            {
                "step_id": "v-1",
                "name": "Unit test check",
                "status": "passed",
                "duration_ms": 500,
            }
        ]
        is_valid, errors = validate_task_feedback(payload)
    def test_validator_field_type_mismatches(self):
        # Bool expected, got int
        p1 = get_valid_task_handoff_payload()
        p1["constraints"]["security_policy"]["allow_network"] = 123
        self.assertFalse(validate_task_handoff(p1)[0])

        # Int expected, got string
        p2 = get_valid_task_handoff_payload()
        p2["constraints"]["limits"]["max_execution_timeout_seconds"] = "not an int"
        self.assertFalse(validate_task_handoff(p2)[0])

        # List expected, got dict
        p3 = get_valid_task_handoff_payload()
        p3["file_operations"] = {"not": "a list"}
        self.assertFalse(validate_task_handoff(p3)[0])

        # Dict expected, got list
        p4 = get_valid_task_handoff_payload()
        p4["context"] = ["not", "a", "dict"]
        self.assertFalse(validate_task_handoff(p4)[0])

    def test_context_optional_fields_and_limits_boundaries(self):
        p = get_valid_task_handoff_payload()
        p["context"]["discussion_summary"] = "A short summary of conversation."
        p["context"]["discussion_history"] = [
            {"role": "user", "content": "Please refactor auth."},
            {"role": "assistant", "content": "Sure, here is the plan."},
        ]
        p["context"]["branch_or_context"] = "main"
        p["context"]["custom_metadata"] = {"tag": "v1"}
        is_valid, errors = validate_task_handoff(p)
        self.assertTrue(is_valid, f"Expected valid context optional fields, got: {errors}")

        # Limit boundaries
        p_bound1 = get_valid_task_handoff_payload()
        p_bound1["constraints"]["limits"]["max_execution_timeout_seconds"] = 0  # < 1
        self.assertFalse(validate_task_handoff(p_bound1)[0])

        p_bound2 = get_valid_task_handoff_payload()
        p_bound2["constraints"]["limits"]["max_execution_timeout_seconds"] = 9999999  # > 86400
        self.assertFalse(validate_task_handoff(p_bound2)[0])


if __name__ == "__main__":
    unittest.main()
