// ==UserScript==
// @name         ChatGPT to Local Agent Bridge
// @namespace    https://local.bridge/chatgpt
// @version      1.0.0
// @description  Handoff decisions and implementation tasks directly from ChatGPT Web to local agents (PI)
// @author       ChatGPT Local Agent Bridge Team
// @match        https://chatgpt.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

(function() {
    'use strict';

    const DEFAULT_DAEMON_URL = 'http://127.0.0.1:8765';
    const CONFIG = {
        get url() { return GM_getValue('bridge_daemon_url', DEFAULT_DAEMON_URL); },
        set url(val) { GM_setValue('bridge_daemon_url', val); },
        get token() { return GM_getValue('bridge_auth_token', ''); },
        set token(val) { GM_setValue('bridge_auth_token', val); }
    };

    // Register Tampermonkey menu commands
    if (typeof GM_registerMenuCommand !== 'undefined') {
        GM_registerMenuCommand('⚙️ Configure Local Bridge Settings', showConfigModal);
    }

    function showConfigModal() {
        let modal = document.getElementById('bridge-config-modal');
        if (modal) modal.remove();

        modal = document.createElement('div');
        modal.id = 'bridge-config-modal';
        modal.style.cssText = `
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: rgba(0,0,0,0.6); z-index: 999999; display: flex;
            align-items: center; justify-content: center; font-family: sans-serif;
        `;

        modal.innerHTML = `
            <div style="background: #1e1e2e; color: #cdd6f4; padding: 24px; border-radius: 12px; width: 420px; box-shadow: 0 8px 32px rgba(0,0,0,0.5);">
                <h3 style="margin-top: 0; color: #a6e3a1; font-size: 18px;">⚡ Local Agent Bridge Configuration</h3>
                <div style="margin-bottom: 14px;">
                    <label style="display: block; font-size: 12px; margin-bottom: 4px; color: #bac2de;">Bridge Daemon URL:</label>
                    <input id="cfg-bridge-url" type="text" value="${CONFIG.url}" style="width: 100%; padding: 8px; border-radius: 6px; border: 1px solid #45475a; background: #313244; color: #fff; box-sizing: border-box;" />
                </div>
                <div style="margin-bottom: 18px;">
                    <label style="display: block; font-size: 12px; margin-bottom: 4px; color: #bac2de;">Bearer Auth Token:</label>
                    <input id="cfg-bridge-token" type="password" value="${CONFIG.token}" placeholder="Paste token here" style="width: 100%; padding: 8px; border-radius: 6px; border: 1px solid #45475a; background: #313244; color: #fff; box-sizing: border-box;" />
                </div>
                <div style="display: flex; justify-content: flex-end; gap: 8px;">
                    <button id="cfg-bridge-cancel" style="padding: 8px 16px; border-radius: 6px; border: none; background: #45475a; color: #cdd6f4; cursor: pointer;">Cancel</button>
                    <button id="cfg-bridge-save" style="padding: 8px 16px; border-radius: 6px; border: none; background: #a6e3a1; color: #11111b; font-weight: bold; cursor: pointer;">Save Settings</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        document.getElementById('cfg-bridge-cancel').onclick = () => modal.remove();
        document.getElementById('cfg-bridge-save').onclick = () => {
            CONFIG.url = document.getElementById('cfg-bridge-url').value.trim();
            CONFIG.token = document.getElementById('cfg-bridge-token').value.trim();
            showToast('✅ Bridge settings saved!', 'success');
            modal.remove();
        };
    }

    function showToast(msg, type = 'info') {
        const toast = document.createElement('div');
        const bg = type === 'success' ? '#a6e3a1' : (type === 'error' ? '#f38ba8' : '#89b4fa');
        const fg = '#11111b';
        toast.style.cssText = `
            position: fixed; bottom: 24px; right: 24px; padding: 12px 20px;
            background: ${bg}; color: ${fg}; font-weight: bold; font-size: 13px;
            border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.3); z-index: 999999;
            transition: opacity 0.3s; font-family: sans-serif;
        `;
        toast.innerText = msg;
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 300);
        }, 3500);
    }

    function extractTaskPayload(messageElement) {
        const codeBlocks = messageElement.querySelectorAll('pre code');
        for (const block of codeBlocks) {
            try {
                const text = block.innerText.trim();
                const parsed = JSON.parse(text);
                if (parsed.schema_version && parsed.task && parsed.context) {
                    return parsed;
                }
                if (parsed.task_id || (parsed.title && parsed.tasks)) {
                    return parsed;
                }
            } catch (e) {}
        }

        // Fallback: construct standard task handoff from message context
        const messageText = messageElement.innerText.trim();
        const sessionId = crypto.randomUUID();
        const taskId = "task-" + Date.now();

        return {
            schema_version: "1.0.0",
            context: {
                session_id: sessionId,
                source: "browser_extension",
                user_intent: "Implement decision from ChatGPT Web interaction",
                decision_rationale: "Extracted directly from ChatGPT conversation message.",
                timestamp: new Date().toISOString()
            },
            task: {
                task_id: taskId,
                title: "ChatGPT Web Task Handoff",
                description: messageText.slice(0, 4000),
                category: "feature",
                priority: "medium",
                complexity: "medium"
            },
            constraints: {
                environment: {
                    os: "any",
                    runtime: "node/python",
                    cwd: "."
                },
                security_policy: {
                    execution_mode: "sandboxed",
                    permitted_commands: ["^python3?\\b", "^pytest\\b", "^git\\b", "^npm\\b"],
                    forbidden_commands: ["\\brm\\s+-rf\\s+[/~]"],
                    allowed_file_paths: ["*"],
                    forbidden_file_paths: [".git/*", ".env*"]
                },
                limits: {
                    max_execution_timeout_seconds: 300,
                    max_iterations: 30,
                    max_file_modifications: 25
                }
            },
            file_operations: [],
            verification: {
                automated_checks: [],
                acceptance_statement: "Task implementation matches ChatGPT conversation instructions."
            }
        };
    }

    function sendToDaemon(payload, buttonElement) {
        if (!CONFIG.token) {
            showConfigModal();
            showToast('⚠️ Please configure your auth token first', 'error');
            return;
        }

        const originalText = buttonElement.innerText;
        buttonElement.innerText = '⏳ Dispatching...';
        buttonElement.disabled = true;

        GM_xmlhttpRequest({
            method: 'POST',
            url: `${CONFIG.url.rstrip ? CONFIG.url.rstrip('/') : CONFIG.url}/api/tasks`,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${CONFIG.token}`
            },
            data: JSON.stringify(payload),
            timeout: 15000,
            onload: (res) => {
                buttonElement.disabled = false;
                if (res.status === 201 || res.status === 202) {
                    try {
                        const data = JSON.parse(res.responseText);
                        buttonElement.innerText = `✅ Queued (${data.task_id.slice(0, 8)})`;
                        buttonElement.style.background = '#10b981';
                        showToast(`🚀 Task ${data.task_id} sent to local agent!`, 'success');
                    } catch (e) {
                        buttonElement.innerText = '✅ Sent';
                    }
                } else {
                    buttonElement.innerText = `❌ Error (${res.status})`;
                    buttonElement.style.background = '#ef4444';
                    showToast(`Error ${res.status}: ${res.responseText.slice(0, 100)}`, 'error');
                    setTimeout(() => { buttonElement.innerText = originalText; buttonElement.style.background = ''; }, 3000);
                }
            },
            onerror: (err) => {
                buttonElement.disabled = false;
                buttonElement.innerText = '❌ Daemon Unreachable';
                buttonElement.style.background = '#ef4444';
                showToast(`Daemon unreachable at ${CONFIG.url}. Is bridge running?`, 'error');
                setTimeout(() => { buttonElement.innerText = originalText; buttonElement.style.background = ''; }, 3000);
            },
            ontimeout: () => {
                buttonElement.disabled = false;
                buttonElement.innerText = '❌ Timeout';
                showToast('Request to local daemon timed out', 'error');
            }
        });
    }

    function injectButtons() {
        const turns = document.querySelectorAll('article[data-testid^="conversation-turn-"]');
        turns.forEach(turn => {
            // Check if assistant turn
            const isAssistant = turn.querySelector('[data-message-author-role="assistant"]') || turn.querySelector('.agent-turn') || turn.innerText.includes('ChatGPT');
            if (!isAssistant) return;

            if (turn.querySelector('.chatgpt-local-bridge-btn')) return;

            const actionsBar = turn.querySelector('div[class*="items-center"]') || turn.querySelector('div[class*="justify-between"]');
            if (!actionsBar) return;

            const btn = document.createElement('button');
            btn.className = 'chatgpt-local-bridge-btn';
            btn.style.cssText = `
                display: inline-flex; align-items: center; gap: 4px;
                padding: 4px 10px; margin-left: 8px; font-size: 12px; font-weight: 600;
                background: #059669; color: #ffffff; border: none; border-radius: 6px;
                cursor: pointer; transition: background 0.2s; font-family: sans-serif;
            `;
            btn.innerText = '⚡ Send to Local Agent';
            btn.onmouseover = () => { if (!btn.disabled) btn.style.background = '#047857'; };
            btn.onmouseout = () => { if (!btn.disabled) btn.style.background = '#059669'; };

            btn.onclick = (e) => {
                e.preventDefault();
                const payload = extractTaskPayload(turn);
                sendToDaemon(payload, btn);
            };

            actionsBar.appendChild(btn);
        });
    }

    // Observe DOM mutations for new turns
    const observer = new MutationObserver(() => injectButtons());
    observer.observe(document.body, { childList: true, subtree: true });
    injectButtons();
})();
