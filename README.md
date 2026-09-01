# ChatGPT Web to Local Agent Research & Bridge System

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero%20(stdlib%20only)-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-90.9%25-brightgreen.svg)]()
[![Tests](https://img.shields.io/badge/tests-158%20passed-success.svg)]()

> **A publication-grade, zero-dependency bridge connecting cloud-based ChatGPT Web sessions directly to local autonomous coding agents (such as PI / local agents), featuring 3 flexible ingress pathways, constant-time HMAC security, strict workspace boundary jailing, and real-time Server-Sent Events (SSE) telemetry.**

---

## Table of Contents

1. [Executive Overview & Problem Statement](#1-executive-overview--problem-statement)
2. [System Architecture](#2-system-architecture)
3. [System Requirements & Zero-Dependency Setup](#3-system-requirements--zero-dependency-setup)
4. [Quickstart Guide (Under 3 Minutes)](#4-quickstart-guide-under-3-minutes)
5. [Step-by-Step Pathway Configuration](#5-step-by-step-pathway-configuration)
   - [Pathway 1: ChatGPT Custom GPT Action Setup (Cloud Tunnel)](#pathway-1-chatgpt-custom-gpt-action-setup-cloud-tunnel)
   - [Pathway 2: Browser Userscript Setup (Direct Loopback)](#pathway-2-browser-userscript-setup-direct-loopback)
   - [Pathway 3: Zero-Network Fallback (Inbox & Clipboard Watcher)](#pathway-3-zero-network-fallback-inbox--clipboard-watcher)
6. [Configuring Local PI & Coding Agents](#6-configuring-local-pi--coding-agents)
7. [Security Architecture & Threat Defense](#7-security-architecture--threat-defense)
8. [Automated Testing & Verification](#8-automated-testing--verification)
9. [Troubleshooting & FAQ](#9-troubleshooting--faq)
10. [Repository Structure](#10-repository-structure)

---

## 1. Executive Overview & Problem Statement

Modern software engineering workflows increasingly rely on Large Language Model (LLM) conversational web interfaces—most notably **ChatGPT Web** (`https://chatgpt.com`)—for high-level ideation, architectural reasoning, requirement formulation, and collaborative decision-making. 

However, a fundamental disconnect separates the cloud-hosted conversational interface from the developer's local workstation where concrete implementation, filesystem modifications, and test suite execution take place.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            TRADITIONAL MANUAL CYCLE                          │
│                                                                              │
│  [ChatGPT Web] ──(Manual Copy)──> [Editor/Terminal] ──(Manual Run)──> [Test] │
│        ▲                                                                │    │
│        └──────────────(Manual Log Transcription / Context Loss)─────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### The Friction of Traditional Workflows
1. **Manual Copy-Paste Overhead**: Transcribing code diffs, file patches, and terminal commands across multiple windows is slow and error-prone.
2. **Context Loss & Hallucination**: Without structured execution context, the cloud LLM cannot reliably verify whether its proposed changes compiled or passed tests.
3. **Fragmented Audit Trail**: No centralized state machine or log tracks what decisions were made in chat versus what was actually executed on disk.
4. **Security Vulnerabilities**: Naive bridge attempts often expose open HTTP ports or pass unvalidated LLM output directly to a system shell (`eval` / `sh -c`), creating severe Remote Code Execution (RCE) hazards.

### The Solution: ChatGPT-to-Local Agent Bridge
This project provides a robust, lightweight, and extensible local bridge system that enables ChatGPT Web to dispatch structured implementation tasks directly to local autonomous agent tools (such as the local `pi` CLI agent or custom test runners) and receive real-time execution telemetry, test results, and file diffs back in the browser.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          AUTOMATED BRIDGE WORKFLOW                           │
│                                                                              │
│  [ChatGPT Web] ──(Structured JSON Handoff)──> [Bridge Daemon (Port 8765)]    │
│        ▲                                                  │                  │
│        │                                        [Security & Path Jail]       │
│        │                                                  │                  │
│        │                                                  ▼                  │
│  [Live SSE Telemetry / Feedback] <──────────── [PI / Local Agent / Runner]   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. System Architecture

The bridge architecture is organized into four distinct, decoupled layers: **Ingestion**, **Security & Validation**, **Lifecycle State Machine**, and **Modular Executors**.

```
+---------------------------------------------------------------------------------------------------+
|                                            CHATGPT WEB                                            |
|                    (High-Level Brainstorming, Architecture Decisions, Task Formulation)           |
+---------------------------------------------------------------------------------------------------+
             │                                    │                                    │
   [Custom GPT Action]                  [Browser Userscript]                 [Clipboard / File Drop]
   (Cloudflare / ngrok Tunnel)          (Direct Loopback / PNA)              (Zero-Network Fallback)
             │                                    │                                    │
             ▼                                    ▼                                    ▼
+---------------------------------------------------------------------------------------------------+
|                                     BRIDGE DAEMON (127.0.0.1:8765)                                |
|                                                                                                   |
|  1. INGESTION LAYER                                                                               |
|     - REST API Server (/api/tasks, /api/tasks/{id}, /health, /confirm, /cancel)                   |
|     - Server-Sent Events (SSE) Stream (/api/tasks/{id}/events)                                    |
|     - Inbox File Watcher (inbox/*.json, inbox/*.md with atomic locking)                           |
|     - System Clipboard Monitor (detects structured JSON/Markdown handoffs)                        |
|                                                                                                   |
|  2. SECURITY & VALIDATION GATE                                                                    |
|     - Constant-Time HMAC Bearer Token Authentication (hmac.compare_digest)                        |
|     - Schema Validator (Authoritative Draft-07 JSON Schema + SemVer / ISO-8601 validation)        |
|     - Path Traversal Jail (pathlib.Path.resolve() workspace boundary containment)                 |
|     - Safe Process Runner (argv vectorization with shell=False, command policy regex filter)      |
|     - Timeout Ladder (SIGTERM graceful termination escalating to SIGKILL)                         |
|     - Human-in-the-Loop (HITL) Execution Confirmation Gate (WAITING_CONFIRMATION)                 |
|                                                                                                   |
|  3. TASK LIFECYCLE & STATE STORE                                                                  |
|     - 10-State Lifecycle Engine (RECEIVED -> VALIDATING -> QUEUED -> RUNNING -> ...)              |
|     - SQLite Persistent Store (WAL mode) / In-Memory Event Streaming Cache                        |
|                                                                                                   |
|  4. MODULAR AGENT EXECUTORS                                                                       |
|     - BaseAgentExecutor Interface                                                                 |
|     - PIAgentExecutor: Non-interactive prompt formatting & execution (`pi -p`)                    |
|     - MockAgentExecutor: Deterministic multi-step simulation & automated verification checks      |
+---------------------------------------------------------------------------------------------------+
             │                                                                         │
             ▼                                                                         ▼
+------------------------------------------+             +------------------------------------------+
|          LOCAL AGENT WORKSPACE           |             |           REAL-TIME TELEMETRY            |
|   (Target Files, Diffs, Source Code)     |             |   (SSE Event Stream, Feedback Logs)      |
+------------------------------------------+             +------------------------------------------+
```

### Core Architecture Components

| Component | Module | Responsibility |
| :--- | :--- | :--- |
| **Bridge Daemon** | `bridge/daemon.py` | Multi-threaded HTTP/SSE server handling CORS, PNA preflights, and request routing. |
| **Security Gate** | `bridge/security.py` | Constant-time token verification, path jail confinement, command whitelisting, and process timeout ladders. |
| **Schema Validator** | `bridge/schema_validator.py` | Authoritative standard-library JSON schema validator enforcing `schema/task_handoff.json`. |
| **State Store** | `bridge/state_store.py` | SQLite state persistence (WAL mode), audit logging, and SSE event streaming. |
| **Agent Executors** | `bridge/executors.py` | Integration for local `pi` CLI agent and deterministic `mock` simulation harnesses. |
| **Inbox Watcher** | `bridge/inbox_watcher.py` | Atomic polling of `inbox/` for JSON files and Markdown fenced blocks. |
| **CLI Client** | `client/trigger_task.py` | Standalone CLI client for task submission, status queries, SSE streaming, and approval. |
| **Userscript** | `client/userscript.js` | Tampermonkey/Violentmonkey script injecting a 1-click execution button in ChatGPT Web. |

---

## 3. System Requirements & Zero-Dependency Setup

### Requirements
- **Operating System**: macOS (Darwin), Linux, or Windows (WSL / native).
- **Python Version**: **Python 3.10+** (tested on 3.10, 3.11, 3.12, 3.13).
- **Zero Third-Party Dependencies**: Built strictly using the Python Standard Library (`http.server`, `sqlite3`, `socket`, `hmac`, `urllib`, `pathlib`, `json`, `subprocess`, `argparse`, `dataclasses`, `shlex`, `threading`, `time`, `logging`).

### Installation
Clone the repository and verify your Python environment:

```bash
git clone https://github.com/your-org/chatgpt_to_local_agent_research.git
cd chatgpt_to_local_agent_research

# Verify Python version (must be >= 3.10)
python3 --version
```

No `pip install`, virtual environment, or external package installation is required!

---

## 4. Quickstart Guide (Under 3 Minutes)

Get a complete end-to-end task execution running locally in three simple steps:

### Step 1: Start the Bridge Daemon
Launch the daemon bound to `127.0.0.1:8765` with an authorized workspace and an authentication token:

```bash
python3 bridge/daemon.py \
  --workspace . \
  --port 8765 \
  --token dev-secret-token-123
```

*Output:*
```
======================================================================
ChatGPT-to-Local Agent Bridge Daemon running at http://127.0.0.1:8765
Authentication Token: dev-secret-token-123
Allowed Workspaces: [PosixPath('/path/to/chatgpt_to_local_agent_research')]
Executor: MOCK
======================================================================
```

### Step 2: Probe Daemon Health
In a separate terminal window, test the daemon health endpoint using the CLI client:

```bash
python3 client/trigger_task.py --token dev-secret-token-123 health
```

*Output:*
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "active_tasks": 0,
  "uptime_seconds": 12.4
}
```

### Step 3: Trigger a Sample Task & Stream Logs
Submit the sample bugfix task (`examples/sample_task_bugfix.json`) with live SSE log streaming:

```bash
python3 client/trigger_task.py submit \
  -f examples/sample_task_bugfix.json \
  --token dev-secret-token-123 \
  --stream
```

*Live Terminal Output:*
```
Task submitted successfully!
  Task ID: task-bugfix-auth-timing-001
  Status:  queued

--- Connected to Live Event Stream for task-bugfix-auth-timing-001 ---
[VALIDATING    ]   5% - Validating task payload
[RUNNING       ]  15% - Starting mock agent execution
  [agent:INFO] Analyzing task objectives: 'Refactor token comparison logic in security module...'
  [agent:INFO] Inspecting file 'bridge/security.py'
  ↳ Check check-hmac-digest: [PASSED]
[SUCCEEDED     ] 100% - Task completed successfully
```

---

## 5. Step-by-Step Pathway Configuration

The system supports **three distinct communication pathways** tailored to different security requirements, network constraints, and user preferences.

```
+----------------------------------------------------------------------------------------------------+
|                                    COMMUNICATION PATHWAY MATRIX                                    |
+-------------------+---------------------+-----------------------+----------------------------------+
| Pathway           | Network Topology    | Browser / Client      | Best Suited For                  |
+-------------------+---------------------+-----------------------+----------------------------------+
| 1. Custom GPT     | Cloud HTTPS Tunnel  | OpenAI Cloud Action   | Autonomous multi-turn workflows, |
|    Action         | (Cloudflare / ngrok)| (No browser plugin)   | remote triggers, ChatGPT Plus    |
+-------------------+---------------------+-----------------------+----------------------------------+
| 2. Browser        | Direct 127.0.0.1    | Tampermonkey Script / | Zero cloud config, sub-ms latency|
|    Userscript     | Loopback HTTP       | Bookmarklet           | interactive 1-click UI in chat   |
+-------------------+---------------------+-----------------------+----------------------------------+
| 3. Zero-Network   | Local File / OS IPC | Native "Copy Code" or | Air-gapped machines, corporate   |
|    Fallback       | (No network ports)  | File Drop into inbox/ | firewalls, strict offline envs   |
+-------------------+---------------------+-----------------------+----------------------------------+
```

---

### Pathway 1: ChatGPT Custom GPT Action Setup (Cloud Tunnel)

Pathway 1 uses OpenAI's **Custom GPT Actions** to allow ChatGPT to dispatch tasks directly over a secure reverse tunnel.

```
[ChatGPT Web] ──> [OpenAI Cloud Action] ──(HTTPS)──> [Tunnel Ingress] ──(Loopback)──> [Bridge Daemon]
```

#### 1. Set Up a Secure Reverse Tunnel
Expose the local daemon port (`8765`) to an HTTPS tunnel endpoint using your preferred tool:

* **Option A: Cloudflare Tunnel (Recommended for high stability & custom domains)**
  ```bash
  cloudflared tunnel --url http://127.0.0.1:8765
  ```
  *(Copy the assigned URL, e.g., `https://random-words.trycloudflare.com`)*

* **Option B: ngrok**
  ```bash
  ngrok http 8765
  ```
  *(Copy the assigned URL, e.g., `https://abc-123.ngrok-free.app`)*

* **Option C: Tailscale Funnel**
  ```bash
  tailscale funnel 8765
  ```

#### 2. Create a Custom GPT in ChatGPT
1. Navigate to **ChatGPT Web** -> **Explore GPTs** -> **Create a GPT** -> click the **Configure** tab.
2. Set **Name**: `Local Agent Dispatcher`.
3. Set **Description**: `Connects architectural decisions in ChatGPT directly to my local coding agent.`
4. Set **Instructions** (paste the system prompt below):

```markdown
You are an expert software architect and engineering coordinator. When the user approves an implementation plan, bugfix, or refactoring task, you MUST format the implementation instructions into a strict, schema-compliant JSON task handoff payload and submit it to the local agent bridge using the `submitTask` action.

Always ensure:
1. `task_id` is a unique string (e.g. `task-YYYYMMDD-###`).
2. `context.workspace_root` points to the developer's authorized repository directory.
3. `constraints` specify clear timeout limits and command policies.
4. `verification_commands` contain executable test commands (e.g., `python3 run_tests.py`).
```

#### 3. Import the OpenAPI Specification
1. In the GPT Editor, scroll to **Actions** and click **Create new action**.
2. Click **Import from URL** or paste the raw contents of [`client/custom_gpt_openapi.json`](client/custom_gpt_openapi.json).
3. In the imported JSON, replace the `servers.url` field with your public tunnel URL (e.g., `https://random-words.trycloudflare.com`).

#### 4. Configure Bearer Authentication
1. In the Action settings under **Authentication**, select **API Key**.
2. Set **Auth Type**: `Bearer`.
3. Paste your secret token (matching the `--token` flag passed to `bridge/daemon.py`).
4. Save and publish your Custom GPT (set access to **Only me**).

---

### Pathway 2: Browser Userscript Setup (Direct Loopback)

Pathway 2 runs entirely inside your browser via a lightweight **Tampermonkey / Violentmonkey** userscript. It monitors the ChatGPT Web DOM and adds a **`⚡ Send to Local Agent`** button directly above any task handoff code block.

```
[ChatGPT Web (DOM)] ──(Click ⚡ Send Button)──> [Userscript (GM_xmlhttpRequest)] ──(127.0.0.1:8765)──> [Bridge Daemon]
```

#### 1. Install Tampermonkey / Violentmonkey
Install the Tampermonkey extension for your browser ([Chrome Web Store](https://chromewebstore.google.com/detail/tampermonkey/dhdgffkkebhmkfjojejmpbldmpobfkfo) | [Firefox Add-ons](https://addons.mozilla.org/en-US/firefox/addon/tampermonkey/)).

#### 2. Install the Userscript
1. Open the Tampermonkey Dashboard -> click the **Utilities** tab -> **Import from File** or click **Create a new script (+)**.
2. Paste the entire contents of [`client/userscript.js`](client/userscript.js).
3. Save the script (Ctrl+S / Cmd+S).

#### 3. Configure Bridge Daemon Settings
1. Open [`https://chatgpt.com`](https://chatgpt.com).
2. Click the Tampermonkey extension icon in your browser toolbar and select **⚙️ Configure Local Bridge Settings**.
3. In the configuration modal, confirm:
   - **Bridge Daemon URL**: `http://127.0.0.1:8765`
   - **Bearer Auth Token**: `<YOUR_SECRET_TOKEN>` (matching daemon `--token`)
4. Click **Save Settings**.

```
┌──────────────────────────────────────────────────────────┐
│         ⚡ Local Agent Bridge Configuration              │
├──────────────────────────────────────────────────────────┤
│ Bridge Daemon URL:                                       │
│ [ http://127.0.0.1:8765                                ] │
│                                                          │
│ Bearer Auth Token:                                       │
│ [ ••••••••••••••••••••                                 ] │
│                                                          │
│                       [ Cancel ]  [ Save Settings (OK) ] │
└──────────────────────────────────────────────────────────┘
```

#### 4. Triggering Tasks from ChatGPT Web
1. When chatting with ChatGPT, ask it to output a task handoff JSON block or use the prompt template from `examples/sample_task_feature.json`.
2. As soon as ChatGPT renders the code block, the userscript automatically injects a floating **`⚡ Send to Local Agent`** badge.
3. Click the badge: the payload is transmitted to the local daemon in sub-milliseconds, and a toast notification displays live execution progress.

*(Alternative: If you cannot install extensions, drag [`client/bookmarklet.js`](client/bookmarklet.js) to your browser bookmarks bar for 1-click execution.)*

---

### Pathway 3: Zero-Network Fallback (Inbox & Clipboard Watcher)

For air-gapped workstations, strict corporate VPNs, or environments where local HTTP listening ports are restricted, Pathway 3 provides a **zero-network file/clipboard ingestion mechanism**.

```
[ChatGPT Web] ──(Copy Code Block)──> [System Clipboard] ──(OS Ingest)──> [Bridge Daemon] ──> [Agent Execution]
                                 OR
[Save Task File] ──(Drop File)─────> [inbox/*.json]     ──(File Watch)──> [Bridge Daemon] ──> [Agent Execution]
```

#### Mode A: Inbox Directory File Watcher
The daemon automatically monitors the `inbox/` directory for incoming JSON or Markdown files:

1. Start the daemon with inbox monitoring enabled (default is `./inbox`):
   ```bash
   python3 bridge/daemon.py --workspace . --inbox ./inbox
   ```

2. Drop any task handoff file into the `inbox/` folder:
   ```bash
   cp examples/sample_task_bugfix.json inbox/
   ```

3. **Atomic Processing**: The daemon locks the file with `.task.json.lock`, extracts the JSON payload (even from embedded markdown code blocks), executes the task, and archives the completed file to `inbox/processed/`. If validation fails, an informative `.error.json` report is generated alongside the file.

#### Mode B: System Clipboard Poller
Enable the clipboard monitor to automatically capture tasks copied to your OS clipboard:

1. Start the daemon with `--clipboard`:
   ```bash
   python3 bridge/daemon.py --workspace . --clipboard
   ```

2. In ChatGPT Web, simply click the standard **"Copy code"** button on a task handoff code block.
3. The daemon detects the structured JSON on the clipboard, validates the schema, prompts for user confirmation in the terminal, and executes the task locally.

---

## 6. Configuring Local PI & Coding Agents

The bridge daemon features a pluggable executor architecture supporting both the **PI Coding Agent** and **Deterministic Mock Simulation**.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                           MODULAR EXECUTOR SUBSYSTEM                           │
│                                                                                │
│                        ┌─────────────────────────┐                             │
│                        │    BaseAgentExecutor    │                             │
│                        └────────────┬────────────┘                             │
│                                     │                                          │
│                 ┌───────────────────┴───────────────────┐                      │
│                 ▼                                       ▼                      │
│     ┌───────────────────────┐               ┌───────────────────────┐          │
│     │    PIAgentExecutor    │               │   MockAgentExecutor   │          │
│     ├───────────────────────┤               ├───────────────────────┤          │
│     │ • Invokes `pi -p`     │               │ • Pure python engine  │          │
│     │ • Formats markdown    │               │ • Synthetic file ops  │          │
│     │ • Non-interactive CLI │               │ • Automated test runs │          │
│     │ • Captures stdout/err │               │ • Ideal for CI & dev  │          │
│     └───────────────────────┘               └───────────────────────┘          │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 1. Configuring the PI Agent (`PIAgentExecutor`)
To dispatch tasks directly to your local installation of the `pi` AI coding agent:

1. Ensure the `pi` binary is installed and executable on your system (e.g. `/opt/homebrew/bin/pi` or `~/.local/bin/pi`):
   ```bash
   which pi
   # /opt/homebrew/bin/pi
   ```

2. Start the daemon with `--executor pi`:
   ```bash
   python3 bridge/daemon.py \
     --executor pi \
     --pi-binary /opt/homebrew/bin/pi \
     --workspace /Users/username/projects/my-app \
     --port 8765 \
     --token secret-token-123
   ```
   *(You can also set the `PI_BINARY_PATH` environment variable instead of `--pi-binary`)*

3. **How Execution Works**:
   - `PIAgentExecutor` formats the structured task payload into a comprehensive, multi-section markdown prompt containing the objective, decision rationale, target file operations, and acceptance criteria.
   - It invokes the `pi` binary in non-interactive mode:
     ```bash
     /opt/homebrew/bin/pi -p "<FORMATTED_PROMPT>" --tools read,edit,write,bash,grep,find,ls --approve
     ```
   - Standard output and error streams are captured line-by-line in real-time and broadcast over the SSE stream to the client.
   - Post-execution, the daemon runs the specified `verification_commands` to ensure all tests pass.

### 2. Configuring the Mock Agent (`MockAgentExecutor`)
The `mock` executor provides a deterministic simulation harness that requires no external AI binaries. It executes synthetic file operations (`create`, `modify`, `delete`, `rename`, `inspect`) and runs real verification test commands:

```bash
python3 bridge/daemon.py \
  --executor mock \
  --workspace . \
  --port 8765 \
  --token secret-token-123
```

---

## 7. Security Architecture & Threat Defense

Bridging a cloud interface to a workstation requires rigorous **defense-in-depth** to prevent Remote Code Execution (RCE), arbitrary file destruction, and timing side-channel exploits.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                            5-TIER DEFENSE-IN-DEPTH GATE                          │
├──────────────────────────────────────────────────────────────────────────────────┤
│  [1] Constant-Time HMAC Auth   ──> Blocks unauthorized requests & timing leaks   │
│  [2] Schema Validation Gate    ──> Rejects malformed or over-sized payloads      │
│  [3] Path Containment Jail     ──> Prevents directory traversal (../../ escapes) │
│  [4] Shell Injection Defense   ──> Enforces argument vectorization (shell=False) │
│  [5] Process Timeout Ladder    ──> Escalates SIGTERM -> SIGKILL on hanging tasks │
│  [6] Human-in-the-Loop (HITL)  ──> Pauses critical operations for user approval  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 1. Constant-Time HMAC Authentication
All incoming requests over HTTP or SSE must provide a valid Bearer token. To prevent **timing side-channel attacks**, token verification strictly utilizes `hmac.compare_digest()`:

```python
# bridge/security.py
def verify_token(provided_token: Optional[str], expected_token: str) -> bool:
    if not provided_token or not expected_token:
        return False
    return hmac.compare_digest(provided_token.strip(), expected_token.strip())
```

### 2. Path Traversal Boundary Jail
Every file operation and workspace target is validated against an explicit whitelist of authorized workspace roots. Path resolution uses `pathlib.Path.resolve()` to resolve symbolic links and relative segments:

```python
# Strict boundary check prevents escapes via ../../etc/passwd or symlink tricks
def validate_path(target_path: str, allowed_roots: List[Path]) -> Path:
    resolved_target = Path(target_path).resolve()
    for root in allowed_roots:
        resolved_root = root.resolve()
        if resolved_target == resolved_root or resolved_root in resolved_target.parents:
            return resolved_target
    raise PathTraversalError(f"Access to '{target_path}' is outside allowed workspaces.")
```

### 3. Shell Injection Prevention
All process executions (`SafeProcessRunner`) strictly enforce **argument vectorization** (`subprocess.Popen(argv, shell=False)`). The system **never** invokes an unrestricted shell or executes raw command strings:

- Commands are validated against regex-based permitted command policies (e.g., `^python3?\b`, `^pytest\b`, `^git\b`).
- Dangerous command patterns (e.g., `rm -rf /`, `mkfs`, `dd`) and null bytes (`\x00`) are rejected immediately.

### 4. Process Timeout Escalation Ladders
To prevent runaway loops or hanging processes from exhausting system resources:
1. When a task reaches its `timeout_seconds` limit, the runner sends **`SIGTERM`** to allow graceful cleanup.
2. If the process does not terminate within a 2-second grace period, the runner escalates to **`SIGKILL`**.

### 5. Human-in-the-Loop (HITL) Confirmation Gate
For sensitive operations (e.g., database schema changes, file deletions, or tasks marked with `require_confirmation: true`), the daemon transitions the task into **`WAITING_CONFIRMATION`** status. Execution halts until the developer explicitly approves the task via the CLI client:

```bash
# Approve a paused task
python3 client/trigger_task.py confirm <TASK_ID> --token dev-secret-token-123
```

---

## 8. Automated Testing & Verification

The bridge codebase includes a comprehensive **4-tier test suite** comprising **158 unit, integration, end-to-end, and adversarial tests** with **zero external PyPI dependencies**.

### Running the Test Suite
Execute the test runner with code coverage enforcement:

```bash
python3 run_tests.py
```

To run with custom coverage thresholds or verbose test output:
```bash
# Enforce >= 90.0% coverage threshold with verbose output
python3 run_tests.py --min-coverage 90.0 -v 2
```

### Test Suite Architecture (158 Tests)

```
================================================================================
 CODE COVERAGE REPORT
================================================================================
Name                                Stmts     Miss    Cover   Missing Lines
--------------------------------------------------------------------------------
bridge/__init__.py                      9        0   100.0%   
bridge/daemon.py                      460       57    87.6%   33, 88-89, 135...
bridge/executors.py                   561       52    90.7%   28-29, 49, 67...
bridge/inbox_watcher.py               154       12    92.2%   21-22, 51-52...
bridge/schema_validator.py            674       78    88.4%   43-44, 49-50...
bridge/security.py                    243       29    88.1%   146-147, 164...
bridge/state_store.py                 557       13    97.7%   274, 301, 334...
--------------------------------------------------------------------------------
TOTAL                                2658      241    90.9%
================================================================================

Test Summary: 158 tests executed in 4.50s (0 errors, 0 failures, 0 skipped)
✅ PASSED: All 158 tests passed with 90.9% coverage (target >= 90.0%).
```

### Multi-Tier Test Breakdown

| Test Tier | Module | Tests | Scope & Focus Areas |
| :--- | :--- | :---: | :--- |
| **Tier 1: Unit & Schema** | `tests/test_schema_validation.py`<br>`tests/test_security.py` | 59 | JSON Schema Draft-07 compliance, SemVer/UUID formats, constant-time HMAC auth, path jail boundary resolution, command vectorization, timeout ladders. |
| **Tier 2: Concurrency & State** | `tests/test_state_store.py`<br>`tests/test_executors.py`<br>`tests/test_inbox_watcher.py` | 51 | SQLite WAL persistence, state transitions, Mock & PI executors, verification checks, atomic `.lock` file handling in `inbox/`. |
| **Tier 3: In-Process HTTP & SSE** | `tests/test_daemon_api.py` | 22 | Socketpair HTTP loopback harness, REST endpoints (`/api/tasks`, `/health`, `/confirm`), PNA & CORS preflights, SSE live event streaming. |
| **Tier 4: E2E & Adversarial** | `tests/test_e2e_workflow.py`<br>`tests/test_adversarial_security.py` | 26 | End-to-end bugfix/feature workflows, path traversal jailbreak attacks (`../../etc/passwd`), symlink attacks, command injection fuzzing, 20-task concurrency stress. |

---

## 9. Troubleshooting & FAQ

### Q1: Why am I getting CORS or Private Network Access (PNA) errors in the browser?
**Cause**: Modern Chromium browsers enforce W3C Private Network Access when a public web page (`https://chatgpt.com`) makes requests to `http://127.0.0.1`.
**Solution**:
1. The bridge daemon automatically emits `Access-Control-Allow-Private-Network: true` on all `OPTIONS` preflight requests.
2. In Tampermonkey, ensure the userscript includes `@grant GM_xmlhttpRequest` and `@connect 127.0.0.1`, which bypasses standard browser CORS restrictions.
3. In Chrome, if testing without Tampermonkey, check `chrome://flags/#block-insecure-private-network-requests` if strict PNA blocks local requests.

### Q2: Error: `Address already in use` on port 8765.
**Cause**: Another instance of `bridge/daemon.py` or another service is listening on port 8765.
**Solution**:
1. Find and terminate the existing process:
   ```bash
   lsof -i :8765
   kill -9 <PID>
   ```
2. Or run the daemon on an alternative port:
   ```bash
   python3 bridge/daemon.py --port 8899
   ```

### Q3: Why does `client/trigger_task.py` return `401 Unauthorized`?
**Cause**: The Bearer token supplied in the client request does not match the daemon's active `--token`.
**Solution**:
1. Pass the exact token via CLI: `--token <TOKEN>`.
2. Or set the environment variable: `export BRIDGE_AUTH_TOKEN="<TOKEN>"`.
3. Check the token printed in the daemon startup banner.

### Q4: Why does a task fail with `ERR_FORBIDDEN_PATH` (HTTP 403)?
**Cause**: The requested file path or `workspace_root` is located outside the directory whitelist specified via `--workspace`.
**Solution**:
Start the daemon with the explicit repository root added:
```bash
python3 bridge/daemon.py --workspace /path/to/my-repo --workspace /another/path
```

### Q5: How do I cancel a runaway task?
**Solution**:
Use the CLI client to request immediate cancellation and process termination:
```bash
python3 client/trigger_task.py cancel <TASK_ID> --token <TOKEN>
```

---

## 10. Repository Structure

```
chatgpt_to_local_agent_research/
├── README.md                      # Comprehensive documentation & setup guide
├── PROJECT.md                     # Master project specification & interface contracts
├── ORIGINAL_REQUEST.md            # Original requirements & acceptance criteria
├── architecture_report.md         # Exhaustive architecture & security report
├── TEST_READY.md                  # Test suite readiness & coverage report
├── TEST_INFRA.md                  # Test infrastructure specification
├── run_tests.py                   # Automated zero-dependency test & coverage runner
│
├── schema/                        # Authoritative JSON & TypeScript Schemas
│   ├── task_handoff.json          # Task handoff schema (Draft-07 compliant)
│   ├── task_feedback.json         # Telemetry & feedback schema
│   └── task_handoff.d.ts          # TypeScript type definitions
│
├── bridge/                        # Core Bridge Daemon Implementation
│   ├── __init__.py                # Package exports
│   ├── daemon.py                  # HTTP REST API & SSE Server
│   ├── security.py                # HMAC auth, path jail & safe process runner
│   ├── schema_validator.py        # Standard-library JSON schema validator
│   ├── state_store.py             # SQLite persistence (WAL) & event streaming
│   ├── executors.py               # PIAgentExecutor & MockAgentExecutor
│   └── inbox_watcher.py           # Atomic directory watcher & markdown parser
│
├── client/                        # Client Tools & Browser Integrations
│   ├── trigger_task.py            # CLI client (submit, status, stream, confirm)
│   ├── userscript.js              # Tampermonkey userscript for ChatGPT Web
│   ├── bookmarklet.js             # Zero-install browser bookmarklet
│   └── custom_gpt_openapi.json    # OpenAPI 3.1.0 specification for Custom GPTs
│
├── examples/                      # Validated Sample Task Payloads
│   ├── sample_task_bugfix.json    # Sample bugfix task payload
│   └── sample_task_feature.json   # Sample feature addition task payload
│
└── tests/                         # 4-Tier Automated Test Suite (158 Tests)
    ├── __init__.py
    ├── test_schema_validation.py  # Schema validation tests
    ├── test_security.py           # HMAC auth & path jail tests
    ├── test_state_store.py        # SQLite persistence & lifecycle tests
    ├── test_executors.py          # PI & Mock executor tests
    ├── test_inbox_watcher.py      # Inbox directory watcher tests
    ├── test_daemon_api.py         # HTTP REST & SSE streaming tests
    ├── test_e2e_workflow.py       # End-to-end integration workflows
    └── test_adversarial_security.py # Adversarial stress & jailbreak tests
```

---

## License

This project is licensed under the [MIT License](LICENSE).
