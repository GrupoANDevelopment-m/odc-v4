"""Web interface for the ODC agent.

A tiny stdlib-only HTTP server with a chat UI. No external deps.
Designed to be the surface the user talks to in natural language
— the LLM does the routing, not the UI.

Run with:
    odc web                 # starts on http://127.0.0.1:8765
    odc web --port 9000     # custom port
    odc web --host 0.0.0.0  # bind to all interfaces
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from odc.observability import get_logger, setup_logging

log = get_logger("odc.web")

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ODC — honest agent</title>
<style>
  :root {
    --bg: #0e0f13;
    --panel: #161821;
    --fg: #e8eaf0;
    --muted: #8b8f9b;
    --accent: #6ee7b7;
    --border: #2a2d3a;
    --err: #fca5a5;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }
  header {
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  header .meta { color: var(--muted); font-size: 12px; }
  main {
    flex: 1;
    overflow-y: auto;
    padding: 24px;
    max-width: 900px;
    width: 100%;
    margin: 0 auto;
  }
  .msg {
    margin-bottom: 16px;
    padding: 14px 18px;
    border-radius: 8px;
    background: var(--panel);
    border: 1px solid var(--border);
  }
  .msg.user { background: #1d2030; }
  .msg.assistant { background: #161821; }
  .msg.system { background: #181a23; color: var(--muted); font-size: 13px; }
  .msg .role { font-size: 11px; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; letter-spacing: 0.05em; }
  .msg pre { background: #0a0b0f; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 13px; }
  .msg code { background: #0a0b0f; padding: 2px 6px; border-radius: 3px; font-size: 13px; }
  .msg pre code { background: transparent; padding: 0; }
  .msg .tools {
    margin-top: 10px;
    font-size: 12px;
    color: var(--muted);
    padding-top: 8px;
    border-top: 1px dashed var(--border);
  }
  .msg .tool-call {
    display: inline-block;
    padding: 2px 6px;
    background: #0a0b0f;
    border-radius: 3px;
    margin-right: 4px;
    color: var(--accent);
  }
  footer {
    border-top: 1px solid var(--border);
    background: var(--panel);
    padding: 12px 24px;
  }
  form {
    display: flex;
    gap: 8px;
    max-width: 900px;
    margin: 0 auto;
  }
  input[type=text] {
    flex: 1;
    background: #0a0b0f;
    color: var(--fg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 14px;
    font: inherit;
    outline: none;
  }
  input[type=text]:focus { border-color: var(--accent); }
  button {
    background: var(--accent);
    color: #0e0f13;
    border: none;
    border-radius: 6px;
    padding: 0 18px;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  .empty { color: var(--muted); text-align: center; margin-top: 80px; }
  .empty h2 { font-weight: 500; font-size: 20px; margin-bottom: 8px; color: var(--fg); }
  .empty p { font-size: 14px; max-width: 480px; margin: 0 auto 12px; line-height: 1.7; }
  .empty code { background: #0a0b0f; padding: 2px 8px; border-radius: 3px; font-size: 13px; }
  .loading { color: var(--muted); font-style: italic; }
  .err { color: var(--err); }
</style>
</head>
<body>
<header>
  <h1>ODC <span style="color: var(--muted); font-weight: 400; font-size: 13px;">— honest agent</span></h1>
  <div class="meta" id="meta">connecting…</div>
</header>
<main id="messages">
  <div class="empty" id="empty">
    <h2>Talk to ODC in natural language.</h2>
    <p>It uses the Fable Method (think → act → prove) with persistent memory, real tools, and the ability to extend itself when it hits a wall.</p>
    <p>Try: <code>find all the TODO comments in odc/ and add a one-line summary to README.md</code></p>
    <p>Or: <code>what's the deploy command for the staging cluster?</code></p>
    <p>Or: <code>access our SAP system at erp.corp.local:8443 with the credentials in .env</code></p>
  </div>
</main>
<footer>
  <form id="form" autocomplete="off">
    <input type="text" id="input" placeholder="ask ODC anything…" autofocus>
    <button type="submit" id="send">send</button>
  </form>
</footer>
<script>
const $ = (id) => document.getElementById(id);
const messages = $('messages');
const empty = $('empty');
const form = $('form');
const input = $('input');
const send = $('send');
const meta = $('meta');

let nextId = 1;
const transcript = [];

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function md(s) {
  // Minimal markdown: code fences, inline code, line breaks.
  let html = escapeHtml(s);
  html = html.replace(/```(\\w*)\\n([\\s\\S]*?)```/g, '<pre><code>$2</code></pre>');
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  html = html.replace(/\\n/g, '<br>');
  return html;
}

function add(role, text, tools) {
  if (empty) empty.remove();
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  let html = `<div class="role">${role}</div><div class="body">${md(text)}</div>`;
  if (tools && tools.length) {
    html += '<div class="tools">tools: ';
    html += tools.map(t => `<span class="tool-call">${escapeHtml(t)}</span>`).join('');
    html += '</div>';
  }
  div.innerHTML = html;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function setMeta(s) { meta.textContent = s; }

async function loadMeta() {
  try {
    const r = await fetch('/api/info');
    if (r.ok) {
      const d = await r.json();
      setMeta(`${d.provider}/${d.model} · ${d.tools} tools · ${d.skills} skills`);
    } else { setMeta('disconnected'); }
  } catch { setMeta('offline'); }
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  send.disabled = true;
  add('user', text);
  transcript.push({ role: 'user', text });
  const placeholder = add('assistant', '<span class="loading">working…</span>');
  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) {
      const err = await r.text();
      placeholder.innerHTML = `<div class="role">error</div><div class="err">${escapeHtml(err)}</div>`;
    } else {
      const d = await r.json();
      placeholder.innerHTML = `<div class="role">assistant</div><div class="body">${md(d.report)}</div>` +
        (d.tools && d.tools.length ? `<div class="tools">turns: ${d.turns} · tools: ${d.tools.map(t => `<span class="tool-call">${escapeHtml(t)}</span>`).join('')}</div>` : '');
      transcript.push({ role: 'assistant', text: d.report });
    }
  } catch (err) {
    placeholder.innerHTML = `<div class="role">error</div><div class="err">${escapeHtml(err.message || err)}</div>`;
  } finally {
    send.disabled = false;
    input.focus();
  }
});

loadMeta();
</script>
</body>
</html>
"""


# Lazy-imported agent to avoid circular imports.
_agent_lock = threading.Lock()
_agent_instance: Any = None


def _get_agent():
    global _agent_instance
    if _agent_instance is None:
        with _agent_lock:
            if _agent_instance is None:
                from odc import Agent
                from odc.config import Config

                cfg = Config()
                cfg.ensure_dirs()
                setup_logging(cfg.log_level, cfg.data_dir / "logs")
                _agent_instance = Agent(config=cfg, auto_approve=True, interactive=False)
    return _agent_instance


def _get_info() -> dict[str, Any]:
    from odc import Config

    cfg = Config()
    return {
        "provider": cfg.llm_provider,
        "model": getattr(_get_agent().provider, "_model", "?"),
        "tools": len(_get_agent().tool_names()),
        "skills": len(_get_agent().skills),
    }


def _run_task(text: str) -> dict[str, Any]:
    agent = _get_agent()
    run = asyncio.run(agent.run(text))
    tools_used: list[str] = []
    for msg in run.result.final_messages:
        if msg.tool_calls:
            tools_used.extend(tc.get("name", "?") for tc in msg.tool_calls)
    return {
        "report": run.result.report,
        "turns": run.result.turns,
        "tool_calls": run.result.tool_calls,
        "tools": tools_used,
        "handed_back": run.result.handed_back_reason,
    }


class Handler(BaseHTTPRequestHandler):
    """One handler for everything: GET /, GET /api/info, POST /api/chat."""

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/index"):
            self._send_text(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/api/info":
            try:
                self._send_json(200, _get_info())
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return
        if self.path == "/favicon.ico":
            self._send_text(204, b"", "image/x-icon")
            return
        self._send_text(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self._send_text(404, b"not found", "text/plain")
            return
        try:
            ln = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(ln).decode("utf-8") if ln else "{}"
            data = json.loads(raw)
            text = (data.get("text") or "").strip()
            if not text:
                self._send_json(400, {"error": "empty text"})
                return
            result = _run_task(text)
            self._send_json(200, result)
        except Exception as e:  # noqa: BLE001
            log.exception("chat handler failed")
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the web server. Blocks."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"ODC web: http://{host}:{port}")
    print("Press Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.shutdown()


def main() -> None:
    p = argparse.ArgumentParser(prog="odc web", description="Web chat for the ODC agent.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
