/**
 * ChatGPT-to-Local Agent 1-Click Bookmarklet
 *
 * To use as a browser bookmarklet:
 * 1. Create a new bookmark in your browser.
 * 2. Paste the contents of this file prefixed with `javascript:` into the URL field.
 * 3. While on ChatGPT Web, click the bookmark to dispatch the active task to your local bridge.
 */
javascript:(function() {
    const DAEMON_URL = localStorage.getItem('bridge_daemon_url') || prompt('Enter Bridge Daemon URL:', 'http://127.0.0.1:8765');
    if (!DAEMON_URL) return;
    localStorage.setItem('bridge_daemon_url', DAEMON_URL);

    let token = localStorage.getItem('bridge_auth_token');
    if (!token) {
        token = prompt('Enter Bridge Bearer Auth Token:');
        if (!token) return;
        localStorage.setItem('bridge_auth_token', token);
    }

    // Find the latest assistant message
    const messages = document.querySelectorAll('article[data-testid^="conversation-turn-"]');
    const lastMsg = messages.length > 0 ? messages[messages.length - 1] : document.body;

    // Search for fenced JSON block
    let payload = null;
    const codeBlocks = lastMsg.querySelectorAll('pre code');
    for (const block of codeBlocks) {
        try {
            const parsed = JSON.parse(block.innerText.trim());
            if (parsed.schema_version && parsed.task && parsed.context) {
                payload = parsed;
                break;
            }
        } catch (e) {}
    }

    // Fallback: package text into task handoff schema
    if (!payload) {
        const text = lastMsg.innerText.trim();
        payload = {
            schema_version: "1.0.0",
            context: {
                session_id: crypto.randomUUID(),
                source: "browser_extension",
                user_intent: "Implement decision from ChatGPT conversation",
                decision_rationale: "Extracted via 1-click Bookmarklet from active conversation turn.",
                timestamp: new Date().toISOString()
            },
            task: {
                task_id: "task-" + Date.now(),
                title: "ChatGPT Bookmarklet Task",
                description: text.slice(0, 3000),
                category: "feature",
                priority: "medium",
                complexity: "medium"
            },
            constraints: {
                environment: { os: "any", runtime: "node/python", cwd: "." },
                security_policy: {
                    execution_mode: "sandboxed",
                    permitted_commands: ["^python3?\\b", "^pytest\\b", "^git\\b"],
                    forbidden_commands: ["\\brm\\s+-rf\\s+[/~]"],
                    allowed_file_paths: ["*"],
                    forbidden_file_paths: [".git/*", ".env*"]
                },
                limits: { max_execution_timeout_seconds: 300, max_iterations: 30, max_file_modifications: 25 }
            },
            file_operations: [],
            verification: {
                automated_checks: [],
                acceptance_statement: "Implementation fulfills instructions from ChatGPT Web."
            }
        };
    }

    // Show floating indicator
    const banner = document.createElement('div');
    banner.style.cssText = 'position:fixed;top:20px;right:20px;background:#10b981;color:#fff;padding:12px 20px;border-radius:8px;font-weight:bold;z-index:9999999;box-shadow:0 4px 12px rgba(0,0,0,0.3);font-family:sans-serif;';
    banner.innerText = '⚡ Sending task to Local Bridge...';
    document.body.appendChild(banner);

    fetch(DAEMON_URL.replace(/\/$/, '') + '/api/tasks', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token
        },
        body: JSON.stringify(payload)
    })
    .then(res => {
        if (res.ok) {
            return res.json().then(data => {
                banner.style.background = '#059669';
                banner.innerText = '✅ Task Dispatched! ID: ' + data.task_id;
            });
        } else {
            return res.json().then(err => {
                banner.style.background = '#ef4444';
                banner.innerText = '❌ Failed (' + res.status + '): ' + (err.message || res.statusText);
            });
        }
    })
    .catch(err => {
        banner.style.background = '#ef4444';
        banner.innerText = '❌ Connection Error: ' + err.message;
    })
    .finally(() => {
        setTimeout(() => banner.remove(), 4000);
    });
})();
