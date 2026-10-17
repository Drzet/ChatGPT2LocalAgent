// ==UserScript==
// @name         ChatGPT to Qwen (explicit assignments only)
// @namespace    https://local.bridge/chatgpt
// @version      1.1.0
// @description  Send completed [QWEN_TASK] assistant messages to the local bridge; ignore all other chat.
// @match        https://chatgpt.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @run-at       document-idle
// ==/UserScript==

(() => {
    'use strict';

    const DAEMON_URL = 'http://127.0.0.1:8765';
    const inFlight = new Set();
    const failedThisSession = new Set();
    const recent = new Set(GM_getValue('qwen_dispatched_ids', []));
    const pending = new WeakSet();

    const config = {
        get url() { return GM_getValue('bridge_daemon_url', DAEMON_URL); },
        get token() { return GM_getValue('bridge_auth_token', ''); }
    };

    function configure() {
        const url = prompt('Local bridge URL', config.url);
        if (url === null) return;
        const token = prompt('Bridge bearer token (stored by userscript manager)', config.token);
        if (token === null) return;
        // Only permit loopback; ChatGPT task contents should not be sent to arbitrary hosts.
        if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/?$/.test(url)) {
            alert('The bridge URL must be a loopback HTTP address with an explicit port.');
            return;
        }
        GM_setValue('bridge_daemon_url', url.replace(/\/$/, ''));
        GM_setValue('bridge_auth_token', token.trim());
        alert('Bridge settings saved.');
    }
    GM_registerMenuCommand('Configure Qwen bridge', configure);

    // Markers must encompass the ENTIRE assistant message, not appear in a quote,
    // explanation, code example or another part of an ordinary conversation.
    function parseMarkedTask(text) {
        const match = text.trim().match(/^\[QWEN_TASK\]\s*\n([\s\S]*?)\n\s*\[\/QWEN_TASK\]$/);
        if (!match) return null;

        const fields = {};
        let key = null;
        for (const line of match[1].split(/\r?\n/)) {
            const entry = line.match(/^([A-Za-z][A-Za-z-]*):\s*(.*)$/);
            if (entry) {
                key = entry[1].toLowerCase();
                if (Object.prototype.hasOwnProperty.call(fields, key)) return null;
                fields[key] = entry[2].trim();
            } else if (key && line.trim()) {
                fields[key] += '\n' + line.trim();
            } else if (line.trim()) {
                return null;
            }
        }

        const id = fields['task-id'];
        const cwd = fields.workspace;
        const task = fields.task;
        const validation = fields.validation;
        const branch = fields.branch;
        if (!id || !/^[A-Za-z0-9_-]{3,64}$/.test(id) ||
            !cwd || !(/^\//.test(cwd) || /^[A-Za-z]:[\\/]/.test(cwd)) ||
            !task || task.length < 10 || !validation || validation.length < 10 ||
            !branch || !/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(branch) ||
            branch.includes('..') || branch === 'main' || branch === 'master') return null;

        const repository = fields.repository || '';
        const title = (fields.title || task.split('\n')[0]).slice(0, 200);
        const instructions = [
            task,
            repository ? 'Repository: ' + repository : '',
            'Work only on branch: ' + branch,
            'Validation: ' + validation,
            'Do not run GitHub Actions, force-push or modify the integration branch.',
            'Report changed files, verification commands and their exit codes.'
        ].filter(Boolean).join('\n\n');

        return {
            schema_version: '1.0.0',
            context: {
                session_id: crypto.randomUUID(),
                source: 'browser_extension',
                timestamp: new Date().toISOString(),
                user_intent: task.slice(0, 900),
                decision_rationale: 'Explicit assistant QWEN_TASK handoff'
            },
            task: {
                task_id: id,
                title: title.length >= 3 ? title : 'Local coding task',
                description: instructions,
                category: 'bugfix',
                priority: 'medium',
                complexity: 'medium'
            },
            constraints: {
                environment: { os: 'linux', runtime: 'local', cwd: cwd },
                security_policy: {
                    execution_mode: 'sandboxed',
                    permitted_commands: ['^git\\b', '^python3?\\b', '^make\\b', '^ninja\\b', '^cmake\\b'],
                    forbidden_commands: ['\\brm\\s+-rf\\s+[/~]', '\\bmkfs\\b'],
                    allowed_file_paths: ['*'],
                    forbidden_file_paths: ['.git/*', '.env*'],
                    network_access: 'local_only'
                },
                limits: {
                    max_execution_timeout_seconds: 3600,
                    max_iterations: 30,
                    max_file_modifications: 25
                }
            },
            file_operations: [],
            verification: {
                automated_checks: [],
                acceptance_statement: validation
            }
        };
    }

    function markDone(id) {
        recent.add(id);
        const ids = Array.from(recent).slice(-500);
        GM_setValue('qwen_dispatched_ids', ids);
    }

    function submit(payload) {
        const id = payload.task.task_id;
        if (recent.has(id) || inFlight.has(id) || failedThisSession.has(id)) return;
        const token = config.token;
        if (!token) {
            failedThisSession.add(id);
            console.warn('[Qwen bridge] Missing bearer token; configure in userscript menu');
            return;
        }
        inFlight.add(id);
        GM_xmlhttpRequest({
            method: 'POST',
            url: config.url.replace(/\/$/, '') + '/api/tasks',
            headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
            data: JSON.stringify(payload),
            timeout: 15000,
            onload: response => {
                inFlight.delete(id);
                if (response.status === 201 || response.status === 202 || response.status === 409) {
                    markDone(id);
                    console.info('[Qwen bridge] Task recorded:', id, 'HTTP', response.status);
                } else {
                    failedThisSession.add(id);
                    console.error('[Qwen bridge] Task rejected:', id, response.status, response.responseText);
                }
            },
            onerror: () => { inFlight.delete(id); failedThisSession.add(id); console.error('[Qwen bridge] Connection failed:', id); },
            ontimeout: () => { inFlight.delete(id); failedThisSession.add(id); console.error('[Qwen bridge] Request timed out:', id); }
        });
    }

    function scan() {
        // Never dispatch while the assistant is still generating.
        if (document.querySelector('[data-testid="stop-button"], button[aria-label="Stop generating"]')) return;
        document.querySelectorAll('[data-message-author-role="assistant"]').forEach(message => {
            if (pending.has(message)) return;
            const snapshot = message.innerText.trim();
            const task = parseMarkedTask(snapshot);
            if (!task || recent.has(task.task.task_id) || failedThisSession.has(task.task.task_id)) return;
            pending.add(message);
            // Debounce completed output. The closing marker is mandatory.
            setTimeout(() => {
                pending.delete(message);
                if (!message.isConnected ||
                    document.querySelector('[data-testid="stop-button"], button[aria-label="Stop generating"]') ||
                    message.innerText.trim() !== snapshot) return;
                submit(task);
            }, 1800);
        });
    }

    // Polling avoids a mutation loop triggered by the userscript itself.
    setInterval(scan, 1500);
    scan();
})();
