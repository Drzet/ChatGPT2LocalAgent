# Project: ChatGPT Web to Local Agent Research & Bridge System

## Architecture
The system bridges cloud-based ChatGPT Web sessions (used for high-level brainstorming, architecture, and decision-making) with a local agent environment (PI / local coding agent) on the developer's machine.

```
+-----------------------------------------------------------------------------+
|                               CHATGPT WEB                                   |
|   (Brainstorming, Task Formulation, Architecture Decisions)                |
+-----------------------------------------------------------------------------+
         |                                |                        |
  [Custom GPT Action]             [Userscript / Extension]     [Clipboard Copy]
  (Cloudflare/ngrok Tunnel)       (Direct Loopback / PNA)      (Zero-Network Fallback)
         |                                |                        |
         v                                v                        v
+-----------------------------------------------------------------------------+
|                          BRIDGE DAEMON (Localhost)                          |
|                                                                             |
|  1. INGESTION LAYER                                                         |
|     - REST API Server (/api/tasks, /api/tasks/{id}, /health)                |
|     - Server-Sent Events (SSE) Stream (/api/tasks/{id}/events)              |
|     - Inbox File Watcher (inbox/*.json with atomic locking)                 |
|     - Clipboard Poller / Fallback Ingestor                                  |
|                                                                             |
|  2. SECURITY & VALIDATION GATE                                              |
|     - Constant-time HMAC Bearer Token Authentication                        |
|     - JSON Schema Validator (Draft-07/2020-12 compliance)                   |
|     - Path Traversal Jail (pathlib.resolve boundary containment)            |
|     - Strict Command Argument Vectorization (shell=False, no shell exec)    |
|     - Timeout Ladders (SIGTERM -> SIGKILL) & Dry-Run Mode                   |
|     - Human-in-the-Loop (HITL) Execution Confirmation Gate                  |
|                                                                             |
|  3. TASK LIFECYCLE & STATE MACHINE                                          |
|     - 8-State Engine (RECEIVED -> VALIDATING -> QUEUED -> RUNNING ->        |
|                       SUCCEEDED / FAILED / WAITING_CONFIRMATION / CANCELLED)|
|     - SQLite State Persistence Engine                                       |
|                                                                             |
|  4. MODULAR AGENT EXECUTORS                                                 |
|     - BaseAgentExecutor Interface                                           |
|     - MockAgentExecutor (deterministic multi-step simulation for testing)   |
|     - PIAgentExecutor (direct integration with local PI CLI agent)          |
+-----------------------------------------------------------------------------+
         |
         v
+-----------------------------------------------------------------------------+
|                         LOCAL AGENT WORKSPACE                               |
|   (PI / Local Coding Agent, Target Files, Test Verification Commands)       |
+-----------------------------------------------------------------------------+
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Architecture Pathway Analysis | In-depth comparative analysis of Custom GPT Actions, Browser Userscript/Extension, Clipboard/File Sync, and MCP Bridge | M1 | Survey |
| 2 | Security & Threat Modeling | Rigorous threat modeling covering RCE prevention, prompt injection, CSRF/PNA, token auth, and sandboxing | M1 | Survey |
| 3 | OpenAPI Custom GPT Specification | OpenAPI 3.1.0 schema definition for ChatGPT Custom GPT Actions to trigger tasks | M1 | Survey |
| 4 | Task Handoff JSON Schema | Comprehensive JSON Schema (`schema/task_handoff.json`) validating context, task, constraints, files, criteria | M2 | Survey |
| 5 | Task Feedback JSON Schema | JSON Schema (`schema/task_feedback.json`) for bidirectional execution telemetry and progress streaming | M2 | Survey |
| 6 | TypeScript Schema Definitions | Strong TypeScript type declarations (`schema/task_handoff.d.ts`) matching JSON schemas | M2 | Survey |
| 7 | Schema Validation Engine | Pure standard library validator for incoming task and feedback payloads | M3 | Survey |
| 8 | REST API Endpoints | HTTP server providing `/api/tasks`, `/api/tasks/{id}`, `/health`, `/api/tasks/{id}/confirm`, `/api/tasks/{id}/cancel` | M3 | Survey |
| 9 | SSE Event Streaming | Server-Sent Events endpoint (`/api/tasks/{id}/events`) for real-time log and status updates | M3 | Survey |
| 10 | Inbox File Watcher | Background daemon component monitoring `inbox/` for JSON drops with atomic locking and processing | M3 | Survey |
| 11 | Clipboard Fallback Monitor | CLI option / component watching system clipboard for structured task markdown/JSON | M3 | Survey |
| 12 | Constant-Time HMAC Bearer Auth | Secure token verification with constant-time comparison against timing attacks | M3 | Survey |
| 13 | Path Traversal Jail Defense | Strict resolution ensuring all file targets remain inside authorized workspace root | M3 | Survey |
| 14 | Safe Command Execution Defense | Enforced argument vectorization (`shell=False`) preventing command injection | M3 | Survey |
| 15 | Timeout & Process Management | Graceful SIGTERM with escalation to SIGKILL on execution timeout | M3 | Survey |
| 16 | 8-State Task Lifecycle Engine | State transitions and audit logging (RECEIVED -> VALIDATING -> QUEUED -> RUNNING -> SUCCEEDED/FAILED/WAITING_CONFIRMATION/CANCELLED) | M3 | Survey |
| 17 | Mock Agent Executor | Deterministic agent simulation harness executing verification commands and reporting status | M3 | Survey |
| 18 | PI Agent Executor | Integration module for local PI agent (`pi -p`) with prompt formatting and process piping | M3 | Survey |
| 19 | Client Trigger Script | Standalone CLI client (`client/trigger_task.py`) for submitting tasks and streaming logs | M3 | Survey |
| 20 | Browser Userscript & Bookmarklet | Tampermonkey userscript and bookmarklet for 1-click task handoff from ChatGPT Web | M3 | Survey |
| 21 | E2E Test Suite (Tiers 1-4) | Comprehensive test suite covering feature coverage, boundary conditions, pairwise combinations, and real-world workloads | M4 | Survey |
| 22 | Automated Coverage Runner | Test runner with code coverage calculation enforcing >90% test coverage | M4 | Survey |
| 23 | Setup & Quickstart Guide | Detailed `README.md` documentation for configuring ChatGPT Web, Tunneling, Userscript, and PI Agent | M5 | Survey |
| 24 | Adversarial Hardening & Final Pass | White-box stress testing, coverage hardening, and final acceptance verification | M6 | Survey |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Architecture & Security Report | `architecture_report.md`, OpenAPI Custom GPT Action spec | none | DONE |
| M2 | Task Handoff & Feedback Schema | `schema/task_handoff.json`, `schema/task_feedback.json`, `schema/task_handoff.d.ts` | none | DONE |
| M3 | Bridge Daemon, Security & Client | `bridge/daemon.py`, `bridge/security.py`, `bridge/schema_validator.py`, `bridge/executors.py`, `bridge/inbox_watcher.py`, `client/trigger_task.py`, `client/userscript.js`, `client/bookmarklet.js` | M2 | DONE |
| M4 | E2E Testing Track & Coverage (>90%) | `tests/test_*.py`, `run_tests.py`, `TEST_INFRA.md`, `TEST_READY.md` | M2, M3 | DONE |
| M5 | Quickstart & Integration Documentation | `README.md`, setup guides, sample task fixtures | M1, M3, M4 | DONE |
| M6 | Final Milestone: 100% E2E Pass & Adversarial Hardening | Verification of 100% E2E pass + Tier 5 adversarial testing (179 tests, 91.7% coverage) | M1, M2, M3, M4, M5 | DONE |

## Interface Contracts
### Client / Web ↔ Bridge Daemon REST API
- `POST /api/tasks`: Submits a task. Header: `Authorization: Bearer <TOKEN>`, `Content-Type: application/json`. Payload conforms to `schema/task_handoff.json`. Returns `201 Created` with `{"task_id": "...", "status": "queued"}` or `202 Accepted` (if confirmation required). Returns `400 Bad Request` with `{"error": "ERR_SCHEMA_VALIDATION", "details": [...]}`.
- `GET /api/tasks/{task_id}`: Returns full task record and status (`{"task_id": "...", "status": "...", "feedback": {...}}`).
- `GET /api/tasks/{task_id}/events`: SSE stream of real-time state changes and stdout/stderr chunks.
- `POST /api/tasks/{task_id}/confirm`: Approves execution for tasks held in `WAITING_CONFIRMATION` using constant-time token comparison.
- `POST /api/tasks/{task_id}/cancel`: Cancels an active or queued task.
- `GET /health`: Returns `200 OK` with `{"status": "healthy", "version": "1.0.0"}`.

### Bridge Daemon ↔ Agent Executors
- `BaseAgentExecutor.execute(task: TaskPayload) -> ExecutionResult`
- `MockAgentExecutor`: Executes defined verification commands, creates/modifies targeted mock files, emits structured progress events.
- `PIAgentExecutor`: Invokes `pi -p` with formatted handoff context, captures stdout/stderr, streams events to task store.

## Code Layout
```
chatgpt_to_local_agent_research/
├── ORIGINAL_REQUEST.md
├── PROJECT.md
├── architecture_report.md
├── README.md
├── schema/
│   ├── task_handoff.json
│   ├── task_feedback.json
│   └── task_handoff.d.ts
├── bridge/
│   ├── __init__.py
│   ├── daemon.py
│   ├── security.py
│   ├── schema_validator.py
│   ├── state_store.py
│   ├── executors.py
│   └── inbox_watcher.py
├── client/
│   ├── trigger_task.py
│   ├── userscript.js
│   ├── bookmarklet.js
│   └── custom_gpt_openapi.json
├── examples/
│   ├── sample_task_bugfix.json
│   └── sample_task_feature.json
├── tests/
│   ├── __init__.py
│   ├── test_schema_validation.py
│   ├── test_security.py
│   ├── test_state_store.py
│   ├── test_executors.py
│   ├── test_inbox_watcher.py
│   ├── test_daemon_api.py
│   ├── test_e2e_workflow.py
│   ├── test_adversarial_security.py
│   └── test_adversarial_tier5.py
├── run_tests.py
├── TEST_INFRA.md
└── TEST_READY.md
```
