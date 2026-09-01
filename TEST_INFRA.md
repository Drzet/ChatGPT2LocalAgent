# Testing Infrastructure & Quality Assurance Specification

## 1. Overview & Architecture

The test infrastructure for the **ChatGPT Web to Local Agent Research & Bridge System** provides comprehensive, multi-tiered verification of the entire bridge daemon, security guardrails, schema validation engine, state storage, ingestion pipelines, execution engines, and end-to-end integration workflows.

### Core Architectural Principles
1. **Zero External Dependencies**: Built entirely upon the Python standard library (`unittest`, `trace`, `http.client`, `sqlite3`, `tempfile`, `threading`, `json`, `pathlib`). Requires no external PyPI packages, ensuring 100% deterministic operation across offline and sandboxed environments.
2. **Deterministic Isolation**: Every test case runs in a completely isolated environment (dedicated temporary directories, in-memory or distinct SQLite database instances, random available loopback ports, and isolated process groups).
3. **Rigorous Coverage Target**: Enforces strict line-level coverage (>90% threshold) across all core bridge modules (`bridge/security.py`, `bridge/schema_validator.py`, `bridge/state_store.py`, `bridge/executors.py`, `bridge/inbox_watcher.py`, `bridge/daemon.py`).
4. **Defense-in-Depth Adversarial Testing**: Dedicated adversarial test tier systematically testing path traversal attacks, command injection exploits, timing side-channel attacks, payload flooding, malformed JSON structures, symlink escapes, and process deadlock conditions.

---

## 2. Test Hierarchy & Tier Structure

```
+-------------------------------------------------------------------------------+
|                       TIER 4: END-TO-END WORKFLOW INTEGRATION                 |
|  - Full lifecycle: Client -> HTTP Daemon -> Queue -> Executor -> Verification |
|  - SSE live stream event delivery & client receipt                            |
|  - Multi-file patch, execution, test verification, and artifact persistence   |
+-------------------------------------------------------------------------------+
                                        |
+---------------------------------------v---------------------------------------+
|                       TIER 3: INGESTION & API INTEGRATION                     |
|  - REST API routes (POST /api/tasks, GET /api/tasks/{id}, /confirm, /cancel)  |
|  - HTTP Error Codes (400, 401, 403, 404, 405, 413, 422)                       |
|  - Server-Sent Events (SSE) streaming (/api/tasks/{id}/events)                |
|  - Inbox File Watcher with atomic lock acquisition, processing, and archiving |
+-------------------------------------------------------------------------------+
                                        |
+---------------------------------------v---------------------------------------+
|                   TIER 2: CORE DOMAIN & EXECUTION SUBSYSTEMS                  |
|  - StateStore: 10 lifecycle states, transitions, concurrency, queries, WAL    |
|  - BaseAgentExecutor / MockAgentExecutor: File operations, verification runs  |
|  - PIAgentExecutor: Process piping, non-interactive flags, stdout/stderr drain|
+-------------------------------------------------------------------------------+
                                        |
+---------------------------------------v---------------------------------------+
|                    TIER 1: VALIDATION & SECURITY GATES                        |
|  - JSON Schema Validator (task_handoff.json & task_feedback.json)             |
|  - Constant-time HMAC token authentication (timing attack immunity)           |
|  - Path Traversal Jail (pathlib.resolve boundary containment)                 |
|  - Safe Command Argument Vectorization (shell=False enforcement)              |
|  - Timeout Ladders (graceful SIGTERM -> forced SIGKILL escalation)            |
+-------------------------------------------------------------------------------+
                                        |
+---------------------------------------v---------------------------------------+
|                       TIER 5: ADVERSARIAL & STRESS TESTS                      |
|  - Malformed payload fuzzing, prompt injection resistance simulation          |
|  - Symlink jumps, directory traversal evasion (UNC, URL encoded, null bytes)  |
|  - High concurrency queue flooding, process group termination resilience      |
+-------------------------------------------------------------------------------+
```

---

## 3. Test Modules & Scope Mapping

| Module | Test Suite File | Tested Components | Verification Objective |
|---|---|---|---|
| **Schema Validation** | `tests/test_schema_validation.py` | `bridge.schema_validator` | Full compliance with `schema/task_handoff.json` & `schema/task_feedback.json`. Validates required keys, type enforcement, enum values, regex patterns, SemVer formats, and array bounds. |
| **Security Guardrails** | `tests/test_security.py` | `bridge.security` | Constant-time HMAC auth (`hmac.compare_digest`), path jail enforcement (`validate_workspace_path`), safe process execution (`shell=False`), command allowlist/denylist filtering, and timeout ladder (`SIGTERM` -> `SIGKILL`). |
| **State Store** | `tests/test_state_store.py` | `bridge.state_store` | SQLite state persistence, 10 task lifecycle states (`received`, `validating`, `queued`, `running`, `waiting_confirmation`, `waiting_user_input`, `succeeded`, `failed`, `cancelled`, `blocked`), event logging, artifact tracking, concurrency locking, and status queries. |
| **Executors** | `tests/test_executors.py` | `bridge.executors` | `MockAgentExecutor` deterministic execution (file creation, modification, deletion, unified diff patching, automated verification assertions) and `PIAgentExecutor` subprocess execution, prompt formatting, tool allowlists, and non-blocking stream piping. |
| **Inbox Watcher** | `tests/test_inbox_watcher.py` | `bridge.inbox_watcher` | Background polling of `inbox/`, `.lock` file concurrency arbitration, markdown frontmatter/fenced block parsing, dispatching to bridge, and archival to `inbox/processed/` and `inbox/failed/`. |
| **Daemon REST API** | `tests/test_daemon_api.py` | `bridge.daemon` | REST endpoints (`POST /api/tasks`, `GET /api/tasks/{id}`, `GET /health`, `POST /confirm`, `POST /cancel`), HTTP error codes (400, 401, 404, 405), PNA/CORS headers, and Server-Sent Events (SSE) streaming. |
| **End-to-End Workflow** | `tests/test_e2e_workflow.py` | Full Bridge Subsystems | Multi-step integration flows: Task handoff submission via client -> Ingestion -> Validation -> State Machine -> Execution -> Automated Verification -> SSE Telemetry -> Final Feedback generation. |
| **Adversarial & Stress** | `tests/test_adversarial_security.py` | Security & Robustness | Symlink escape attacks, directory traversal vectors (`..`, null bytes, absolute paths), command injection payload vectors, malformed payloads, rapid queue flooding, and unexpected worker crash recovery. |

---

## 4. Test Design Methodologies

### 4.1 Category-Partition Method
Input domains for each component are systematically partitioned into discrete categories:
- **Authentication**: Valid bearer token, invalid token, empty token, missing header, malformed header format, extra whitespace.
- **Paths**: Workspace-relative path, nested subdirectory path, path escaping via `../`, absolute paths outside workspace, symlinks pointing to `/etc` or `/tmp`, paths with null bytes.
- **Commands**: Whitelisted commands with safe args, non-whitelisted binaries, commands with shell metacharacters (`;`, `|`, `&&`, `` ` ``), commands with malicious flags.
- **Task Lifecycle**: All 10 valid states, valid transition edges (e.g. `received` -> `validating` -> `queued` -> `running` -> `succeeded`), and invalid transition edges (e.g. `succeeded` -> `running`).

### 4.2 Boundary Value Analysis (BVA)
Boundary conditions tested across parameters:
- **String Lengths**: Task title minLength (3), maxLength (256); Task description minLength (10); user_intent minLength (3), maxLength (1000).
- **Numbers & Limits**: Progress percent at 0, 1, 99, 100, -1 (invalid), 101 (invalid); Timeout at 1s, 60s, 86400s, 0s (invalid), 86401s (invalid).
- **Arrays & Collections**: Empty arrays, single item, maximum items, duplicate items.
- **Payload Limits**: Max HTTP payload size (5MB boundary), oversized request bodies (>5MB -> 413 Payload Too Large).

### 4.3 Pairwise Combinations
Systematic matrix testing across operational dimensions:
- `execution_mode` (`standard`, `dry_run`) $\times$ `require_confirmation` (`True`, `False`) $\times$ `executor` (`mock`, `pi`) $\times$ `outcome` (`success`, `fail_verification`, `timeout`, `cancel`).

### 4.4 Real-World Workloads
Real developer scenarios simulated:
- **Bug Fix Workflow**: Context containing error traceback -> Modify source file -> Run unit test verification -> Verify test passes -> Generate feedback with test report artifact.
- **Feature Addition Workflow**: Create new module file -> Add export to `__init__.py` -> Run verification command -> Confirm success.
- **Human-in-the-Loop Confirmation Workflow**: Task with destructive commands paused in `waiting_confirmation` -> User inspects task via API -> Approves via `POST /confirm` -> Resumes to `running` -> Completes.

---

## 5. Coverage Runner & Enforcement (`run_tests.py`)

### 5.1 Architecture
The test runner `run_tests.py` is a standalone Python utility utilizing standard library `unittest` and `trace.Trace`:
1. Discovers and runs all unit, integration, and adversarial tests under `tests/`.
2. Traces code execution at the line level across all modules in `bridge/`.
3. Compiles line counts (executable lines, covered lines, missed lines) per module.
4. Generates a formatted ASCII coverage table and summary.
5. Enforces the strict **>= 90.0%** total coverage threshold.
6. Returns exit code `0` on 100% test pass and meeting coverage target; returns `1` on any test failure or coverage drop.

### 5.2 Invocation Commands
```bash
# Run complete test suite with coverage calculation and threshold enforcement
python3 run_tests.py

# Run standard unittest runner without coverage
python3 -m unittest discover tests

# Run a specific test module
python3 -m unittest tests/test_schema_validation.py
python3 -m unittest tests/test_security.py
python3 -m unittest tests/test_state_store.py
python3 -m unittest tests/test_executors.py
python3 -m unittest tests/test_inbox_watcher.py
python3 -m unittest tests/test_daemon_api.py
python3 -m unittest tests/test_e2e_workflow.py
python3 -m unittest tests/test_adversarial_security.py
```

---

## 6. Test Isolation & Cleanup Guarantees

1. **Filesystem Isolation**: All test files, workspaces, and inbox directories are created inside `tempfile.TemporaryDirectory()` contexts and purged upon test cleanup (`tearDown` / `addCleanup`).
2. **Network Isolation**: HTTP Daemon tests bind to `127.0.0.1` on port `0` (OS-assigned ephemeral port) to avoid port collisions during parallel test runs.
3. **Database Isolation**: Each StateStore test operates against `:memory:` or an isolated temporary SQLite database file.
4. **Thread & Process Isolation**: All background threads (inbox watcher, SSE streams, executor processes) implement explicit shutdown events and timeouts to prevent hanging processes or resource leaks.
