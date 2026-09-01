"""Schema validation module for task handoff and task feedback payloads.

Zero-dependency standard library implementation matching:
- schema/task_handoff.json
- schema/task_feedback.json
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

SEMVER_REGEX = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
UUID_REGEX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DATETIME_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)
TASK_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")
TAG_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")
SHA256_REGEX = re.compile(r"^[a-fA-F0-9]{64}$")


class SchemaValidationError(Exception):
    """Raised when payload validation fails against schema specifications."""

    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors: List[str] = errors or [message]


class Validator:
    """Core validator helper collecting descriptive path-indexed error messages."""

    def __init__(self) -> None:
        self.errors: List[str] = []

    def add_error(self, path: str, msg: str) -> None:
        self.errors.append(f"[{path}] {msg}")

    def check_type(self, path: str, val: Any, expected_type: Any, type_name: str) -> bool:
        if expected_type is bool and not isinstance(val, bool):
            self.add_error(path, f"Expected boolean, got {type(val).__name__}")
            return False
        if expected_type is int and (isinstance(val, bool) or not isinstance(val, int)):
            self.add_error(path, f"Expected integer, got {type(val).__name__}")
            return False
        if expected_type is float and (isinstance(val, bool) or not isinstance(val, (int, float))):
            self.add_error(path, f"Expected number, got {type(val).__name__}")
            return False
        if expected_type is str and not isinstance(val, str):
            self.add_error(path, f"Expected string, got {type(val).__name__}")
            return False
        if expected_type is list and not isinstance(val, list):
            self.add_error(path, f"Expected array/list, got {type(val).__name__}")
            return False
        if expected_type is dict and not isinstance(val, dict):
            self.add_error(path, f"Expected object/dict, got {type(val).__name__}")
            return False
        return True

    def check_enum(self, path: str, val: Any, allowed: Sequence[Any]) -> None:
        if val not in allowed:
            self.add_error(path, f"Value '{val}' is not one of allowed values: {list(allowed)}")

    def check_regex(self, path: str, val: str, regex: re.Pattern, desc: str) -> None:
        if not isinstance(val, str) or not regex.match(val):
            self.add_error(path, f"Value '{val}' does not match required format ({desc})")

    def check_string_len(
        self,
        path: str,
        val: str,
        min_len: Optional[int] = None,
        max_len: Optional[int] = None,
    ) -> None:
        if not isinstance(val, str):
            return
        if min_len is not None and len(val) < min_len:
            self.add_error(path, f"String length ({len(val)}) is less than minimum allowed ({min_len})")
        if max_len is not None and len(val) > max_len:
            self.add_error(path, f"String length ({len(val)}) exceeds maximum allowed ({max_len})")

    def check_num_range(
        self,
        path: str,
        val: Union[int, float],
        min_val: Optional[Union[int, float]] = None,
        max_val: Optional[Union[int, float]] = None,
    ) -> None:
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            return
        if min_val is not None and val < min_val:
            self.add_error(path, f"Value {val} is less than minimum {min_val}")
        if max_val is not None and val > max_val:
            self.add_error(path, f"Value {val} is greater than maximum {max_val}")


def validate_task_handoff(data: Any) -> Tuple[bool, List[str]]:
    """Validate a dictionary payload against schema/task_handoff.json.

    Args:
        data: Parsed dictionary payload.

    Returns:
        Tuple of (is_valid: bool, errors: List[str]).
    """
    v = Validator()
    if not v.check_type("$", data, dict, "object"):
        return False, v.errors

    # Top-level required fields
    required_top = [
        "schema_version",
        "context",
        "task",
        "constraints",
        "file_operations",
        "verification",
    ]
    for req in required_top:
        if req not in data:
            v.add_error("$", f"Missing required top-level field: '{req}'")

    allowed_top = set(required_top) | {"execution_options", "extensions"}
    for k in data.keys():
        if k not in allowed_top:
            v.add_error("$", f"Unexpected property: '{k}' (additionalProperties: false)")

    # 1. schema_version
    if "schema_version" in data:
        sv = data["schema_version"]
        if v.check_type("$.schema_version", sv, str, "string"):
            v.check_regex("$.schema_version", sv, SEMVER_REGEX, "SemVer string (e.g. 1.0.0)")

    # 2. context
    if "context" in data:
        ctx = data["context"]
        if v.check_type("$.context", ctx, dict, "object"):
            req_ctx = ["session_id", "source", "user_intent", "decision_rationale", "timestamp"]
            for req in req_ctx:
                if req not in ctx:
                    v.add_error("$.context", f"Missing required context field: '{req}'")

            allowed_ctx = set(req_ctx) | {
                "conversation_id",
                "message_id",
                "discussion_summary",
                "discussion_history",
                "branch_or_context",
                "custom_metadata",
            }
            for k in ctx.keys():
                if k not in allowed_ctx:
                    v.add_error("$.context", f"Unexpected property in context: '{k}'")

            if "session_id" in ctx:
                sid = ctx["session_id"]
                if v.check_type("$.context.session_id", sid, str, "string"):
                    v.check_regex("$.context.session_id", sid, UUID_REGEX, "UUID")

            if "conversation_id" in ctx and ctx["conversation_id"] is not None:
                v.check_type("$.context.conversation_id", ctx["conversation_id"], str, "string")

            if "message_id" in ctx and ctx["message_id"] is not None:
                v.check_type("$.context.message_id", ctx["message_id"], str, "string")

            if "timestamp" in ctx:
                ts = ctx["timestamp"]
                if v.check_type("$.context.timestamp", ts, str, "string"):
                    v.check_regex("$.context.timestamp", ts, DATETIME_REGEX, "ISO-8601 date-time")

            if "source" in ctx:
                src = ctx["source"]
                if v.check_type("$.context.source", src, str, "string"):
                    v.check_enum(
                        "$.context.source",
                        src,
                        [
                            "chatgpt_web_action",
                            "browser_extension",
                            "clipboard_sync",
                            "mcp_gateway",
                            "file_sync",
                            "cli",
                            "manual",
                        ],
                    )

            if "user_intent" in ctx:
                ui = ctx["user_intent"]
                if v.check_type("$.context.user_intent", ui, str, "string"):
                    v.check_string_len("$.context.user_intent", ui, min_len=3, max_len=1000)

            if "decision_rationale" in ctx:
                dr = ctx["decision_rationale"]
                if v.check_type("$.context.decision_rationale", dr, str, "string"):
                    v.check_string_len("$.context.decision_rationale", dr, min_len=5)

            if "discussion_summary" in ctx and ctx["discussion_summary"] is not None:
                v.check_type("$.context.discussion_summary", ctx["discussion_summary"], str, "string")

            if "discussion_history" in ctx and ctx["discussion_history"] is not None:
                dh = ctx["discussion_history"]
                if v.check_type("$.context.discussion_history", dh, list, "array"):
                    for idx, item in enumerate(dh):
                        p = f"$.context.discussion_history[{idx}]"
                        if v.check_type(p, item, dict, "object"):
                            for req_dh in ["role", "content"]:
                                if req_dh not in item:
                                    v.add_error(p, f"Missing required field: '{req_dh}'")
                            for k in item.keys():
                                if k not in ["role", "content", "timestamp"]:
                                    v.add_error(p, f"Unexpected property: '{k}'")
                            if "role" in item:
                                v.check_enum(f"{p}.role", item["role"], ["system", "user", "assistant", "tool"])
                            if "content" in item:
                                v.check_type(f"{p}.content", item["content"], str, "string")
                            if "timestamp" in item and item["timestamp"] is not None:
                                v.check_regex(f"{p}.timestamp", item["timestamp"], DATETIME_REGEX, "ISO-8601 date-time")

            if "branch_or_context" in ctx and ctx["branch_or_context"] is not None:
                v.check_type("$.context.branch_or_context", ctx["branch_or_context"], str, "string")

            if "custom_metadata" in ctx and ctx["custom_metadata"] is not None:
                v.check_type("$.context.custom_metadata", ctx["custom_metadata"], dict, "object")

    # 3. task
    if "task" in data:
        t = data["task"]
        if v.check_type("$.task", t, dict, "object"):
            req_t = ["task_id", "title", "description", "category", "priority", "complexity"]
            for req in req_t:
                if req not in t:
                    v.add_error("$.task", f"Missing required task field: '{req}'")

            allowed_t = set(req_t) | {"tags", "dependencies", "estimated_duration_seconds"}
            for k in t.keys():
                if k not in allowed_t:
                    v.add_error("$.task", f"Unexpected property in task: '{k}'")

            if "task_id" in t:
                tid = t["task_id"]
                if v.check_type("$.task.task_id", tid, str, "string"):
                    v.check_regex("$.task.task_id", tid, TASK_ID_REGEX, "pattern ^[a-zA-Z0-9_-]{3,64}$")

            if "title" in t:
                title = t["title"]
                if v.check_type("$.task.title", title, str, "string"):
                    v.check_string_len("$.task.title", title, min_len=3, max_len=256)

            if "description" in t:
                desc = t["description"]
                if v.check_type("$.task.description", desc, str, "string"):
                    v.check_string_len("$.task.description", desc, min_len=10)

            if "category" in t:
                cat = t["category"]
                if v.check_type("$.task.category", cat, str, "string"):
                    v.check_enum(
                        "$.task.category",
                        cat,
                        [
                            "feature",
                            "bugfix",
                            "refactor",
                            "test",
                            "documentation",
                            "investigation",
                            "migration",
                            "chore",
                            "security",
                        ],
                    )

            if "priority" in t:
                prio = t["priority"]
                if v.check_type("$.task.priority", prio, str, "string"):
                    v.check_enum("$.task.priority", prio, ["critical", "high", "medium", "low"])

            if "complexity" in t:
                comp = t["complexity"]
                if v.check_type("$.task.complexity", comp, str, "string"):
                    v.check_enum("$.task.complexity", comp, ["trivial", "small", "medium", "large", "epic"])

            if "tags" in t and t["tags"] is not None:
                tags = t["tags"]
                if v.check_type("$.task.tags", tags, list, "array"):
                    for idx, tag in enumerate(tags):
                        p = f"$.task.tags[{idx}]"
                        if v.check_type(p, tag, str, "string"):
                            v.check_regex(p, tag, TAG_REGEX, "pattern ^[a-zA-Z0-9_-]+$")

            if "dependencies" in t and t["dependencies"] is not None:
                deps = t["dependencies"]
                if v.check_type("$.task.dependencies", deps, list, "array"):
                    for idx, dep in enumerate(deps):
                        v.check_type(f"$.task.dependencies[{idx}]", dep, str, "string")

            if "estimated_duration_seconds" in t and t["estimated_duration_seconds"] is not None:
                eds = t["estimated_duration_seconds"]
                if v.check_type("$.task.estimated_duration_seconds", eds, int, "integer"):
                    v.check_num_range("$.task.estimated_duration_seconds", eds, min_val=1)

    # 4. constraints
    if "constraints" in data:
        c = data["constraints"]
        if v.check_type("$.constraints", c, dict, "object"):
            req_c = ["environment", "security_policy", "limits"]
            for req in req_c:
                if req not in c:
                    v.add_error("$.constraints", f"Missing required constraints field: '{req}'")

            allowed_c = set(req_c) | {"deterministic_parameters"}
            for k in c.keys():
                if k not in allowed_c:
                    v.add_error("$.constraints", f"Unexpected property in constraints: '{k}'")

            # constraints.environment
            if "environment" in c:
                env = c["environment"]
                if v.check_type("$.constraints.environment", env, dict, "object"):
                    req_env = ["os", "runtime", "cwd"]
                    for req in req_env:
                        if req not in env:
                            v.add_error("$.constraints.environment", f"Missing required field: '{req}'")
                    allowed_env = set(req_env) | {"env_vars"}
                    for k in env.keys():
                        if k not in allowed_env:
                            v.add_error("$.constraints.environment", f"Unexpected property: '{k}'")
                    if "os" in env:
                        v.check_enum("$.constraints.environment.os", env["os"], ["mac", "linux", "windows", "any"])
                    if "runtime" in env:
                        v.check_type("$.constraints.environment.runtime", env["runtime"], str, "string")
                    if "cwd" in env:
                        v.check_type("$.constraints.environment.cwd", env["cwd"], str, "string")
                    if "env_vars" in env and env["env_vars"] is not None:
                        ev = env["env_vars"]
                        if v.check_type("$.constraints.environment.env_vars", ev, dict, "object"):
                            for ek, ev_val in ev.items():
                                v.check_type(f"$.constraints.environment.env_vars.{ek}", ev_val, str, "string")

            # constraints.security_policy
            if "security_policy" in c:
                sp = c["security_policy"]
                if v.check_type("$.constraints.security_policy", sp, dict, "object"):
                    req_sp = [
                        "execution_mode",
                        "permitted_commands",
                        "forbidden_commands",
                        "allowed_file_paths",
                        "forbidden_file_paths",
                    ]
                    for req in req_sp:
                        if req not in sp:
                            v.add_error("$.constraints.security_policy", f"Missing required field: '{req}'")
                    allowed_sp = set(req_sp) | {"network_access"}
                    for k in sp.keys():
                        if k not in allowed_sp:
                            v.add_error("$.constraints.security_policy", f"Unexpected property: '{k}'")
                    if "execution_mode" in sp:
                        v.check_enum(
                            "$.constraints.security_policy.execution_mode",
                            sp["execution_mode"],
                            ["dry_run", "sandboxed", "interactive_approval", "autonomous"],
                        )
                    if "network_access" in sp and sp["network_access"] is not None:
                        v.check_enum(
                            "$.constraints.security_policy.network_access",
                            sp["network_access"],
                            ["none", "local_only", "full"],
                        )
                    for array_field in ["permitted_commands", "forbidden_commands", "allowed_file_paths", "forbidden_file_paths"]:
                        if array_field in sp:
                            arr = sp[array_field]
                            if v.check_type(f"$.constraints.security_policy.{array_field}", arr, list, "array"):
                                for idx, elem in enumerate(arr):
                                    v.check_type(f"$.constraints.security_policy.{array_field}[{idx}]", elem, str, "string")

            # constraints.limits
            if "limits" in c:
                lim = c["limits"]
                if v.check_type("$.constraints.limits", lim, dict, "object"):
                    req_lim = ["max_execution_timeout_seconds", "max_iterations", "max_file_modifications"]
                    for req in req_lim:
                        if req not in lim:
                            v.add_error("$.constraints.limits", f"Missing required limits field: '{req}'")
                    allowed_lim = set(req_lim) | {"max_token_budget"}
                    for k in lim.keys():
                        if k not in allowed_lim:
                            v.add_error("$.constraints.limits", f"Unexpected property in limits: '{k}'")
                    if "max_execution_timeout_seconds" in lim:
                        mto = lim["max_execution_timeout_seconds"]
                        if v.check_type("$.constraints.limits.max_execution_timeout_seconds", mto, int, "integer"):
                            v.check_num_range("$.constraints.limits.max_execution_timeout_seconds", mto, min_val=1, max_val=86400)
                    if "max_iterations" in lim:
                        mi = lim["max_iterations"]
                        if v.check_type("$.constraints.limits.max_iterations", mi, int, "integer"):
                            v.check_num_range("$.constraints.limits.max_iterations", mi, min_val=1, max_val=1000)
                    if "max_file_modifications" in lim:
                        mfm = lim["max_file_modifications"]
                        if v.check_type("$.constraints.limits.max_file_modifications", mfm, int, "integer"):
                            v.check_num_range("$.constraints.limits.max_file_modifications", mfm, min_val=1, max_val=500)
                    if "max_token_budget" in lim and lim["max_token_budget"] is not None:
                        mtb = lim["max_token_budget"]
                        if v.check_type("$.constraints.limits.max_token_budget", mtb, int, "integer"):
                            v.check_num_range("$.constraints.limits.max_token_budget", mtb, min_val=100)

            # constraints.deterministic_parameters
            if "deterministic_parameters" in c and c["deterministic_parameters"] is not None:
                dp = c["deterministic_parameters"]
                if v.check_type("$.constraints.deterministic_parameters", dp, dict, "object"):
                    allowed_dp = {"temperature", "seed", "strict_mode"}
                    for k in dp.keys():
                        if k not in allowed_dp:
                            v.add_error("$.constraints.deterministic_parameters", f"Unexpected property: '{k}'")
                    if "temperature" in dp and dp["temperature"] is not None:
                        temp = dp["temperature"]
                        if v.check_type("$.constraints.deterministic_parameters.temperature", temp, float, "number"):
                            v.check_num_range("$.constraints.deterministic_parameters.temperature", temp, min_val=0.0, max_val=1.0)
                    if "seed" in dp and dp["seed"] is not None:
                        v.check_type("$.constraints.deterministic_parameters.seed", dp["seed"], int, "integer")
                    if "strict_mode" in dp and dp["strict_mode"] is not None:
                        v.check_type("$.constraints.deterministic_parameters.strict_mode", dp["strict_mode"], bool, "boolean")

    # 5. file_operations
    if "file_operations" in data:
        fo_list = data["file_operations"]
        if v.check_type("$.file_operations", fo_list, list, "array"):
            for idx, fo in enumerate(fo_list):
                p = f"$.file_operations[{idx}]"
                if v.check_type(p, fo, dict, "object"):
                    req_fo = ["path", "operation", "is_critical"]
                    for req in req_fo:
                        if req not in fo:
                            v.add_error(p, f"Missing required file_operation field: '{req}'")
                    allowed_fo = set(req_fo) | {
                        "target_path",
                        "description",
                        "line_ranges",
                        "content",
                        "diff_patch",
                        "expected_sha256_before",
                        "expected_sha256_after",
                    }
                    for k in fo.keys():
                        if k not in allowed_fo:
                            v.add_error(p, f"Unexpected property in file_operation: '{k}'")

                    if "path" in fo:
                        fpath = fo["path"]
                        if v.check_type(f"{p}.path", fpath, str, "string"):
                            v.check_string_len(f"{p}.path", fpath, min_len=1)

                    if "operation" in fo:
                        op = fo["operation"]
                        if v.check_type(f"{p}.operation", op, str, "string"):
                            v.check_enum(f"{p}.operation", op, ["create", "modify", "delete", "inspect", "rename", "patch"])

                    if "is_critical" in fo:
                        v.check_type(f"{p}.is_critical", fo["is_critical"], bool, "boolean")

                    if "target_path" in fo and fo["target_path"] is not None:
                        v.check_type(f"{p}.target_path", fo["target_path"], str, "string")

                    if "description" in fo and fo["description"] is not None:
                        v.check_type(f"{p}.description", fo["description"], str, "string")

                    if "content" in fo and fo["content"] is not None:
                        v.check_type(f"{p}.content", fo["content"], str, "string")

                    if "diff_patch" in fo and fo["diff_patch"] is not None:
                        v.check_type(f"{p}.diff_patch", fo["diff_patch"], str, "string")

                    if "expected_sha256_before" in fo and fo["expected_sha256_before"] is not None:
                        sha = fo["expected_sha256_before"]
                        if v.check_type(f"{p}.expected_sha256_before", sha, str, "string"):
                            v.check_regex(f"{p}.expected_sha256_before", sha, SHA256_REGEX, "64-char hex SHA256")

                    if "expected_sha256_after" in fo and fo["expected_sha256_after"] is not None:
                        sha = fo["expected_sha256_after"]
                        if v.check_type(f"{p}.expected_sha256_after", sha, str, "string"):
                            v.check_regex(f"{p}.expected_sha256_after", sha, SHA256_REGEX, "64-char hex SHA256")

                    if "line_ranges" in fo and fo["line_ranges"] is not None:
                        lr_list = fo["line_ranges"]
                        if v.check_type(f"{p}.line_ranges", lr_list, list, "array"):
                            for l_idx, lr in enumerate(lr_list):
                                lp = f"{p}.line_ranges[{l_idx}]"
                                if v.check_type(lp, lr, dict, "object"):
                                    for req_lr in ["start_line", "end_line"]:
                                        if req_lr not in lr:
                                            v.add_error(lp, f"Missing required line_range field: '{req_lr}'")
                                    for k in lr.keys():
                                        if k not in ["start_line", "end_line"]:
                                            v.add_error(lp, f"Unexpected property in line_range: '{k}'")
                                    if "start_line" in lr:
                                        sl = lr["start_line"]
                                        if v.check_type(f"{lp}.start_line", sl, int, "integer"):
                                            v.check_num_range(f"{lp}.start_line", sl, min_val=1)
                                    if "end_line" in lr:
                                        el = lr["end_line"]
                                        if v.check_type(f"{lp}.end_line", el, int, "integer"):
                                            v.check_num_range(f"{lp}.end_line", el, min_val=1)

    # 6. verification
    if "verification" in data:
        ver = data["verification"]
        if v.check_type("$.verification", ver, dict, "object"):
            req_ver = ["automated_checks", "acceptance_statement"]
            for req in req_ver:
                if req not in ver:
                    v.add_error("$.verification", f"Missing required verification field: '{req}'")
            allowed_ver = set(req_ver) | {"manual_verification_steps"}
            for k in ver.keys():
                if k not in allowed_ver:
                    v.add_error("$.verification", f"Unexpected property in verification: '{k}'")

            if "acceptance_statement" in ver:
                acc = ver["acceptance_statement"]
                if v.check_type("$.verification.acceptance_statement", acc, str, "string"):
                    v.check_string_len("$.verification.acceptance_statement", acc, min_len=10)

            if "manual_verification_steps" in ver and ver["manual_verification_steps"] is not None:
                mvs = ver["manual_verification_steps"]
                if v.check_type("$.verification.manual_verification_steps", mvs, list, "array"):
                    for idx, step in enumerate(mvs):
                        v.check_type(f"$.verification.manual_verification_steps[{idx}]", step, str, "string")

            if "automated_checks" in ver:
                ac_list = ver["automated_checks"]
                if v.check_type("$.verification.automated_checks", ac_list, list, "array"):
                    for idx, ac in enumerate(ac_list):
                        p = f"$.verification.automated_checks[{idx}]"
                        if v.check_type(p, ac, dict, "object"):
                            req_ac = ["id", "name", "type", "is_blocking"]
                            for req in req_ac:
                                if req not in ac:
                                    v.add_error(p, f"Missing required automated check field: '{req}'")
                            allowed_ac = set(req_ac) | {
                                "command",
                                "target_path",
                                "pattern",
                                "expected_exit_code",
                                "expected_stdout_contains",
                                "expected_stdout_matches",
                                "forbidden_stderr_contains",
                                "timeout_seconds",
                            }
                            for k in ac.keys():
                                if k not in allowed_ac:
                                    v.add_error(p, f"Unexpected property in automated_check: '{k}'")

                            if "id" in ac:
                                acid = ac["id"]
                                if v.check_type(f"{p}.id", acid, str, "string"):
                                    v.check_regex(f"{p}.id", acid, TAG_REGEX, "pattern ^[a-zA-Z0-9_-]+$")

                            if "name" in ac:
                                acname = ac["name"]
                                if v.check_type(f"{p}.name", acname, str, "string"):
                                    v.check_string_len(f"{p}.name", acname, min_len=3)

                            if "type" in ac:
                                actype = ac["type"]
                                if v.check_type(f"{p}.type", actype, str, "string"):
                                    v.check_enum(
                                        f"{p}.type",
                                        actype,
                                        ["command", "file_exists", "file_contains", "file_matches_regex", "http_check", "test_suite"],
                                    )

                            if "is_blocking" in ac:
                                v.check_type(f"{p}.is_blocking", ac["is_blocking"], bool, "boolean")

                            if "command" in ac and ac["command"] is not None:
                                v.check_type(f"{p}.command", ac["command"], str, "string")

                            if "target_path" in ac and ac["target_path"] is not None:
                                v.check_type(f"{p}.target_path", ac["target_path"], str, "string")

                            if "pattern" in ac and ac["pattern"] is not None:
                                v.check_type(f"{p}.pattern", ac["pattern"], str, "string")

                            if "expected_exit_code" in ac and ac["expected_exit_code"] is not None:
                                v.check_type(f"{p}.expected_exit_code", ac["expected_exit_code"], int, "integer")

                            if "expected_stdout_contains" in ac and ac["expected_stdout_contains"] is not None:
                                esc = ac["expected_stdout_contains"]
                                if v.check_type(f"{p}.expected_stdout_contains", esc, list, "array"):
                                    for s_idx, s_val in enumerate(esc):
                                        v.check_type(f"{p}.expected_stdout_contains[{s_idx}]", s_val, str, "string")

                            if "expected_stdout_matches" in ac and ac["expected_stdout_matches"] is not None:
                                v.check_type(f"{p}.expected_stdout_matches", ac["expected_stdout_matches"], str, "string")

                            if "forbidden_stderr_contains" in ac and ac["forbidden_stderr_contains"] is not None:
                                fsc = ac["forbidden_stderr_contains"]
                                if v.check_type(f"{p}.forbidden_stderr_contains", fsc, list, "array"):
                                    for s_idx, s_val in enumerate(fsc):
                                        v.check_type(f"{p}.forbidden_stderr_contains[{s_idx}]", s_val, str, "string")

                            if "timeout_seconds" in ac and ac["timeout_seconds"] is not None:
                                to_sec = ac["timeout_seconds"]
                                if v.check_type(f"{p}.timeout_seconds", to_sec, int, "integer"):
                                    v.check_num_range(f"{p}.timeout_seconds", to_sec, min_val=1, max_val=1800)

    # 7. execution_options
    if "execution_options" in data and data["execution_options"] is not None:
        eo = data["execution_options"]
        if v.check_type("$.execution_options", eo, dict, "object"):
            allowed_eo = {"auto_rollback_on_failure", "notify_channel", "create_git_branch"}
            for k in eo.keys():
                if k not in allowed_eo:
                    v.add_error("$.execution_options", f"Unexpected property: '{k}'")
            if "auto_rollback_on_failure" in eo and eo["auto_rollback_on_failure"] is not None:
                v.check_type("$.execution_options.auto_rollback_on_failure", eo["auto_rollback_on_failure"], bool, "boolean")
            if "notify_channel" in eo and eo["notify_channel"] is not None:
                v.check_enum("$.execution_options.notify_channel", eo["notify_channel"], ["websocket", "webhook", "desktop_notification", "none"])
            if "create_git_branch" in eo and eo["create_git_branch"] is not None:
                v.check_type("$.execution_options.create_git_branch", eo["create_git_branch"], str, "string")

    # 8. extensions
    if "extensions" in data and data["extensions"] is not None:
        v.check_type("$.extensions", data["extensions"], dict, "object")

    return (len(v.errors) == 0), v.errors


def validate_task_feedback(data: Any) -> Tuple[bool, List[str]]:
    """Validate a dictionary payload against schema/task_feedback.json.

    Args:
        data: Parsed dictionary payload.

    Returns:
        Tuple of (is_valid: bool, errors: List[str]).
    """
    v = Validator()
    if not v.check_type("$", data, dict, "object"):
        return False, v.errors

    req_top = [
        "schema_version",
        "event_id",
        "task_id",
        "timestamp",
        "state",
        "progress",
        "summary",
    ]
    for req in req_top:
        if req not in data:
            v.add_error("$", f"Missing required top-level field: '{req}'")

    allowed_top = set(req_top) | {
        "logs",
        "artifacts",
        "verification_results",
        "error",
        "user_prompt",
        "custom_data",
    }
    for k in data.keys():
        if k not in allowed_top:
            v.add_error("$", f"Unexpected property: '{k}' (additionalProperties: false)")

    if "schema_version" in data:
        sv = data["schema_version"]
        if v.check_type("$.schema_version", sv, str, "string"):
            v.check_regex("$.schema_version", sv, SEMVER_REGEX, "SemVer string (e.g. 1.0.0)")

    if "event_id" in data:
        eid = data["event_id"]
        if v.check_type("$.event_id", eid, str, "string"):
            v.check_regex("$.event_id", eid, UUID_REGEX, "UUID")

    if "task_id" in data:
        tid = data["task_id"]
        if v.check_type("$.task_id", tid, str, "string"):
            v.check_regex("$.task_id", tid, TASK_ID_REGEX, "pattern ^[a-zA-Z0-9_-]{3,64}$")

    if "timestamp" in data:
        ts = data["timestamp"]
        if v.check_type("$.timestamp", ts, str, "string"):
            v.check_regex("$.timestamp", ts, DATETIME_REGEX, "ISO-8601 date-time")

    if "state" in data:
        st = data["state"]
        if v.check_type("$.state", st, str, "string"):
            v.check_enum(
                "$.state",
                st,
                [
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
                ],
            )

    if "progress" in data:
        prog = data["progress"]
        if v.check_type("$.progress", prog, dict, "object"):
            req_p = ["percent", "current_step", "total_steps", "completed_steps"]
            for req in req_p:
                if req not in prog:
                    v.add_error("$.progress", f"Missing required progress field: '{req}'")
            allowed_p = set(req_p) | {"estimated_remaining_seconds"}
            for k in prog.keys():
                if k not in allowed_p:
                    v.add_error("$.progress", f"Unexpected property in progress: '{k}'")
            if "percent" in prog:
                pct = prog["percent"]
                if v.check_type("$.progress.percent", pct, int, "integer"):
                    v.check_num_range("$.progress.percent", pct, min_val=0, max_val=100)
            if "current_step" in prog:
                v.check_type("$.progress.current_step", prog["current_step"], str, "string")
            if "total_steps" in prog:
                ts = prog["total_steps"]
                if v.check_type("$.progress.total_steps", ts, int, "integer"):
                    v.check_num_range("$.progress.total_steps", ts, min_val=0)
            if "completed_steps" in prog:
                cs = prog["completed_steps"]
                if v.check_type("$.progress.completed_steps", cs, int, "integer"):
                    v.check_num_range("$.progress.completed_steps", cs, min_val=0)
            if "estimated_remaining_seconds" in prog and prog["estimated_remaining_seconds"] is not None:
                ers = prog["estimated_remaining_seconds"]
                if v.check_type("$.progress.estimated_remaining_seconds", ers, int, "integer"):
                    v.check_num_range("$.progress.estimated_remaining_seconds", ers, min_val=0)

    if "summary" in data:
        v.check_type("$.summary", data["summary"], str, "string")

    if "logs" in data and data["logs"] is not None:
        logs = data["logs"]
        if v.check_type("$.logs", logs, list, "array"):
            for idx, item in enumerate(logs):
                p = f"$.logs[{idx}]"
                if v.check_type(p, item, dict, "object"):
                    req_l = ["timestamp", "level", "source", "message"]
                    for req in req_l:
                        if req not in item:
                            v.add_error(p, f"Missing required log field: '{req}'")
                    allowed_l = set(req_l) | {"metadata"}
                    for k in item.keys():
                        if k not in allowed_l:
                            v.add_error(p, f"Unexpected property in log: '{k}'")
                    if "timestamp" in item:
                        v.check_regex(f"{p}.timestamp", item["timestamp"], DATETIME_REGEX, "ISO-8601 date-time")
                    if "level" in item:
                        v.check_enum(f"{p}.level", item["level"], ["debug", "info", "warn", "error"])
                    if "source" in item:
                        v.check_enum(f"{p}.source", item["source"], ["agent", "stdout", "stderr", "system", "verifier"])
                    if "message" in item:
                        v.check_type(f"{p}.message", item["message"], str, "string")
                    if "metadata" in item and item["metadata"] is not None:
                        v.check_type(f"{p}.metadata", item["metadata"], dict, "object")

    if "artifacts" in data and data["artifacts"] is not None:
        arts = data["artifacts"]
        if v.check_type("$.artifacts", arts, list, "array"):
            for idx, art in enumerate(arts):
                p = f"$.artifacts[{idx}]"
                if v.check_type(p, art, dict, "object"):
                    req_a = ["path", "type", "size_bytes"]
                    for req in req_a:
                        if req not in art:
                            v.add_error(p, f"Missing required artifact field: '{req}'")
                    allowed_a = set(req_a) | {"sha256", "description"}
                    for k in art.keys():
                        if k not in allowed_a:
                            v.add_error(p, f"Unexpected property in artifact: '{k}'")
                    if "path" in art:
                        v.check_type(f"{p}.path", art["path"], str, "string")
                    if "type" in art:
                        v.check_enum(f"{p}.type", art["type"], ["source_code", "patch", "test_report", "log", "documentation", "binary"])
                    if "size_bytes" in art:
                        sb = art["size_bytes"]
                        if v.check_type(f"{p}.size_bytes", sb, int, "integer"):
                            v.check_num_range(f"{p}.size_bytes", sb, min_val=0)
                    if "sha256" in art and art["sha256"] is not None:
                        v.check_regex(f"{p}.sha256", art["sha256"], SHA256_REGEX, "64-char hex SHA256")
                    if "description" in art and art["description"] is not None:
                        v.check_type(f"{p}.description", art["description"], str, "string")

    if "verification_results" in data and data["verification_results"] is not None:
        vrs = data["verification_results"]
        if v.check_type("$.verification_results", vrs, list, "array"):
            for idx, vr in enumerate(vrs):
                p = f"$.verification_results[{idx}]"
                if v.check_type(p, vr, dict, "object"):
                    req_vr = ["step_id", "name", "status", "duration_ms"]
                    for req in req_vr:
                        if req not in vr:
                            v.add_error(p, f"Missing required verification_result field: '{req}'")
                    allowed_vr = set(req_vr) | {"exit_code", "stdout_snippet", "stderr_snippet", "failure_reason"}
                    for k in vr.keys():
                        if k not in allowed_vr:
                            v.add_error(p, f"Unexpected property in verification_result: '{k}'")
                    if "step_id" in vr:
                        v.check_type(f"{p}.step_id", vr["step_id"], str, "string")
                    if "name" in vr:
                        v.check_type(f"{p}.name", vr["name"], str, "string")
                    if "status" in vr:
                        v.check_enum(f"{p}.status", vr["status"], ["passed", "failed", "skipped", "timed_out"])
                    if "duration_ms" in vr:
                        dur = vr["duration_ms"]
                        if v.check_type(f"{p}.duration_ms", dur, int, "integer"):
                            v.check_num_range(f"{p}.duration_ms", dur, min_val=0)
                    if "exit_code" in vr and vr["exit_code"] is not None:
                        v.check_type(f"{p}.exit_code", vr["exit_code"], int, "integer")
                    if "stdout_snippet" in vr and vr["stdout_snippet"] is not None:
                        v.check_type(f"{p}.stdout_snippet", vr["stdout_snippet"], str, "string")
                    if "stderr_snippet" in vr and vr["stderr_snippet"] is not None:
                        v.check_type(f"{p}.stderr_snippet", vr["stderr_snippet"], str, "string")
                    if "failure_reason" in vr and vr["failure_reason"] is not None:
                        v.check_type(f"{p}.failure_reason", vr["failure_reason"], str, "string")

    if "error" in data and data["error"] is not None:
        err = data["error"]
        if v.check_type("$.error", err, dict, "object"):
            req_err = ["code", "message", "is_fatal"]
            for req in req_err:
                if req not in err:
                    v.add_error("$.error", f"Missing required error field: '{req}'")
            allowed_err = set(req_err) | {"stack_trace", "remediation_suggestion"}
            for k in err.keys():
                if k not in allowed_err:
                    v.add_error("$.error", f"Unexpected property in error: '{k}'")
            if "code" in err:
                v.check_enum(
                    "$.error.code",
                    err["code"],
                    [
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
                    ],
                )
            if "message" in err:
                v.check_type("$.error.message", err["message"], str, "string")
            if "is_fatal" in err:
                v.check_type("$.error.is_fatal", err["is_fatal"], bool, "boolean")
            if "stack_trace" in err and err["stack_trace"] is not None:
                v.check_type("$.error.stack_trace", err["stack_trace"], str, "string")
            if "remediation_suggestion" in err and err["remediation_suggestion"] is not None:
                v.check_type("$.error.remediation_suggestion", err["remediation_suggestion"], str, "string")

    if "user_prompt" in data and data["user_prompt"] is not None:
        up = data["user_prompt"]
        if v.check_type("$.user_prompt", up, dict, "object"):
            req_up = ["prompt_id", "prompt_type", "message"]
            for req in req_up:
                if req not in up:
                    v.add_error("$.user_prompt", f"Missing required user_prompt field: '{req}'")
            allowed_up = set(req_up) | {"choices", "default_value", "timeout_seconds"}
            for k in up.keys():
                if k not in allowed_up:
                    v.add_error("$.user_prompt", f"Unexpected property in user_prompt: '{k}'")
            if "prompt_id" in up:
                v.check_type("$.user_prompt.prompt_id", up["prompt_id"], str, "string")
            if "prompt_type" in up:
                v.check_enum("$.user_prompt.prompt_type", up["prompt_type"], ["confirm", "select", "text_input", "secret_input"])
            if "message" in up:
                v.check_type("$.user_prompt.message", up["message"], str, "string")
            if "choices" in up and up["choices"] is not None:
                choices = up["choices"]
                if v.check_type("$.user_prompt.choices", choices, list, "array"):
                    for idx, c in enumerate(choices):
                        v.check_type(f"$.user_prompt.choices[{idx}]", c, str, "string")
            if "default_value" in up and up["default_value"] is not None:
                v.check_type("$.user_prompt.default_value", up["default_value"], str, "string")
            if "timeout_seconds" in up and up["timeout_seconds"] is not None:
                to = up["timeout_seconds"]
                if v.check_type("$.user_prompt.timeout_seconds", to, int, "integer"):
                    v.check_num_range("$.user_prompt.timeout_seconds", to, min_val=1)

    if "custom_data" in data and data["custom_data"] is not None:
        v.check_type("$.custom_data", data["custom_data"], dict, "object")

    return (len(v.errors) == 0), v.errors


def parse_and_validate_task_handoff(raw_json: Union[str, bytes, dict]) -> dict:
    """Parse raw JSON string or dict and validate as task handoff payload.

    Raises:
        SchemaValidationError: If JSON is invalid or fails schema rules.
    """
    if isinstance(raw_json, (str, bytes)):
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise SchemaValidationError(f"Invalid JSON: {exc}") from exc
    elif isinstance(raw_json, dict):
        data = raw_json
    else:
        raise SchemaValidationError(f"Expected JSON string, bytes, or dict, got: {type(raw_json).__name__}")

    valid, errors = validate_task_handoff(data)
    if not valid:
        raise SchemaValidationError("Task handoff schema validation failed", errors=errors)
    return data
