# Sol High → local Qwen coding agent on Lynx

Status: integration branch implementation, **not yet validated on Lynx**.
No GitHub Actions or OpenAI API key required.

## Architecture

ChatGPT web (Sol High) → Tampermonkey userscript → localhost bridge
→ OpenCode CLI → locally hosted Qwen inference → local kernel worktree.

The browser script only transmits assistant responses consisting entirely of one
explicitly marked task. Ordinary chat never becomes an instruction to execute.
The local executor is OpenCode, not PI. GitHub remains the code-review path.

## Prepare Lynx (WSL2)

1. Install a working llama.cpp server and OpenCode CLI. Verify the OpenCode
   noninteractive run command exists before continuing.
2. Serve local Qwen through an OpenAI-compatible endpoint on 127.0.0.1:8080.
   Check the exact model ID through its /v1/models endpoint. Both the inference
   server and OpenCode must be reachable from the same WSL environment.
3. Configure OpenCode's local provider in the worktree. For example, this
   illustrative opencode.json needs an actual installed model ID and limits:

~~~json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "llama.cpp": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local llama-server",
      "options": { "baseURL": "http://127.0.0.1:8080/v1" },
      "models": {
        "qwen-local": {
          "name": "Local Qwen",
          "limit": { "context": 32768, "output": 8192 }
        }
      }
    }
  }
}
~~~

   The configured model key must be one the local endpoint accepts. The
   bridge option --opencode-model uses the provider/model naming format
   (for example llama.cpp/qwen-local). Do not silently fall back to a
   cloud model.

4. Clone the bridge repository into WSL and start its daemon. Pass a
   **dedicated Git worktree** for kernel jobs, not the integration checkout:

~~~sh
python3 bridge/daemon.py --executor opencode \
  --opencode-model llama.cpp/qwen-local \
  --workspace /absolute/path/to/dedicated/kernel-worktree \
  --no-inbox --host 127.0.0.1 --port 8765
~~~

   The daemon generates or loads a bridge token, which the userscript needs.
   Use the token from startup output. Bind the daemon to loopback only.

5. Install Tampermonkey in the browser showing ChatGPT and install
   client/userscript.js from this branch. Select its Configure Qwen bridge
   menu item and enter the token and loopback URL.

## Exact task format

Only a **completed assistant response** with precisely these outer markers
is dispatched. Do not wrap it in Markdown code fences or add explanatory text
outside the markers. The complete message must look like this:

~~~text
[QWEN_TASK]
Task-ID: kernel-kconfig-001
Workspace: /absolute/path/to/dedicated/kernel-worktree
Repository: Drzet/linux
Branch: qwen/kconfig-repair
Title: Repair combined radio Kconfig mismatch
Task: Inspect why CONFIG_PCI_PWRCTRL_PWRSEQ resolves to m instead of y.
 Make the smallest justified source or config correction. Do not rerun unrelated jobs.
Validation: Compile the specific affected configuration and record the exit status.
[/QWEN_TASK]
~~~

Mandatory fields: Task-ID, Workspace, Branch, Task, Validation.
Task-ID must be unique per execution (including retries). Workspace must
be an absolute path inside one of the daemon's permitted workspaces.
Never use main/master as the target branch. HTTP 409 indicates an
existing task ID and does not schedule it twice.

The assistant may discuss architecture normally without triggering execution.
To dispatch, request an exact QWEN_TASK response. After Qwen commits and pushes
to its isolated branch, ask Sol to review it using the existing GitHub connection.

## Operating rules

- GitHub Actions remain disabled for this workflow.
- No force-pushes and no writes directly to integration branches.
- Do not run multiple agents in the same writable checkout.
- The task's sandboxed metadata is **not an OS sandbox**. OpenCode model
  tool permissions and local filesystem credentials determine what Qwen can
  execute. Start with a disposable worktree and limited Git credentials.
- The browser script is silent on normal chat. Dispatch errors appear in
  the browser console. Failed submissions are not endlessly retried.
- Local inference has **not** been exercised on Lynx. First validate a
  harmless local test task before permitting kernel modifications.

## Verification without a GPU

~~~sh
python3 run_tests.py
~~~

This validates the bridge and command construction, not model quality,
OpenCode tool calling, actual kernel compilation or Git pushing.
