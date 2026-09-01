"""ChatGPT-to-Local Agent Bridge Package.

Provides secure HTTP/SSE gateway, schema validation, task persistence,
and local agent executors bridging ChatGPT Web to local agents (PI).
"""

from bridge.daemon import BridgeDaemon, BridgeHTTPServer
from bridge.executors import (
    BaseAgentExecutor,
    MockAgentExecutor,
    PIAgentExecutor,
    TaskRunnerPool,
)
from bridge.inbox_watcher import InboxWatcher
from bridge.schema_validator import (
    SchemaValidationError,
    validate_task_feedback,
    validate_task_handoff,
)
from bridge.security import (
    AuthenticationError,
    CommandPolicy,
    CommandSecurityError,
    PathTraversalError,
    ProcessResult,
    ProcessTimeoutError,
    SafeProcessRunner,
    SecurityError,
    extract_bearer_token,
    generate_secure_token,
    validate_path,
    verify_token,
)
from bridge.state_store import LogEntry, StateStore, TaskRecord, TaskState

__version__ = "1.0.0"

__all__ = [
    "BridgeDaemon",
    "BridgeHTTPServer",
    "BaseAgentExecutor",
    "MockAgentExecutor",
    "PIAgentExecutor",
    "TaskRunnerPool",
    "InboxWatcher",
    "SchemaValidationError",
    "validate_task_handoff",
    "validate_task_feedback",
    "SecurityError",
    "AuthenticationError",
    "PathTraversalError",
    "CommandSecurityError",
    "ProcessTimeoutError",
    "SafeProcessRunner",
    "CommandPolicy",
    "ProcessResult",
    "extract_bearer_token",
    "generate_secure_token",
    "validate_path",
    "verify_token",
    "StateStore",
    "TaskRecord",
    "TaskState",
    "LogEntry",
]
