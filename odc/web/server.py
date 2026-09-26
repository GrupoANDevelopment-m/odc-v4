"""Web interface for the ODC agent — premium 3D immersive UI.

Serves the dark/glassmorphic interface with a Three.js DNA-helix
background, exposed under /.

Endpoints:
  GET  /                          — main HTML (3D UI)
  GET  /api/info                   — provider/model/tools/skills
  GET  /api/metrics                — live session metrics
  POST /api/chat                   — run a turn (async, returns report)
  GET  /api/sessions               — list sessions
  POST /api/sessions/new           — start new session
  POST /api/sessions/select        — switch session
  GET  /api/models                 — list local Ollama models
  POST /api/models/use             — set active local model
  POST /api/models/pull            — pull a new model
  POST /api/models/delete          — remove a model
  GET  /api/training/stats         — dataset stats
  POST /api/training/export        — export curated dataset
  POST /api/training/start         — start fine-tune
  GET  /api/training/status        — last fine-tune status

Run with:
    odc web                 # http://127.0.0.1:8765
    odc web --port 9000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from odc.observability import get_logger, setup_logging

log = get_logger("odc.web")

# ── Resolve paths to bundled assets ─────────────────────────────
_HERE = Path(__file__).resolve().parent
INDEX_HTML = (_HERE / "index.html").read_text(encoding="utf-8")

# Lazy agent import (avoid circular)
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


def _run_task(text: str, session_id: str | None = None) -> dict[str, Any]:
    agent = _get_agent()
    run = asyncio.run(agent.run(text, session_id=session_id))
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
        "session_id": getattr(run, "session_id", session_id),
    }


def _get_metrics() -> dict[str, Any]:
    """Live session metrics — pulls from ops store + agent state."""
    from odc import Config
    from odc.observability.ops import OpsStore

    cfg = Config()
    ops_path = cfg.data_dir / "ops" / "metrics.db"
    brain = {"name": "primary", "detail": cfg.llm_provider}

    if ops_path.exists():
        try:
            store = OpsStore(ops_path)
            summary = store.summary()
        except Exception:
            summary = {}
    else:
        summary = {}

    # Check local brain
    try:
        from odc.llm.local import LocalBrain
        brain_status = LocalBrain(cfg).status()
        if brain_status.get("installed"):
            brain = {"name": "local", "detail": f"{brain_status['installed']} model(s)"}
        else:
            brain = {"name": "primary", "detail": cfg.llm_provider}
    except Exception:
        pass

    return {
        "turns": summary.get("turns", 0),
        "llm_calls": summary.get("llm_calls", 0),
        "tool_calls": summary.get("tool_calls", 0),
        "tokens_in": summary.get("tokens_in", 0),
        "tokens_out": summary.get("tokens_out", 0),
        "cost_usd": summary.get("cost_usd", 0.0),
        "p95_ms": summary.get("p95_ms"),
        "brain": brain,
    }


def _list_sessions() -> dict[str, Any]:
    """List persisted sessions from Osiris memory."""
    try:
        from odc import Config
        from odc.mcp.osiris import OsirisMemory

        cfg = Config()
        mem = OsirisMemory(cfg.data_dir / "memory" / "osiris.db")
        rows = mem.list_sessions(limit=50)
        return {
            "sessions": [
                {
                    "id": r.get("id", ""),
                    "title": r.get("title", "(untitled)"),
                    "turns": r.get("turn_count", 0),
                    "age": _age_str(r.get("updated_at")),
                    "active": r.get("active", False),
                }
                for r in rows
            ]
        }
    except Exception as e:
        log.warning("list_sessions failed: %s", e)
        return {"sessions": []}


def _age_str(ts: int | None) -> str:
    if not ts:
        return "—"
    import time
    delta = max(0, int(time.time()) - ts)
    if delta < 60: return f"{delta}s ago"
    if delta < 3600: return f"{delta//60}m ago"
    if delta < 86400: return f"{delta//3600}h ago"
    return f"{delta//86400}d ago"


class Handler(BaseHTTPRequestHandler):
    """Single handler — routes by path."""

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    # ── helpers ──
    def _send_json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        ln = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(ln).decode("utf-8") if ln else "{}"
        return json.loads(raw)

    # ── GET routes ──
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                self._send_text(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/info":
                self._send_json(200, _get_info())
            elif path == "/api/metrics":
                self._send_json(200, _get_metrics())
            elif path == "/api/sessions":
                self._send_json(200, _list_sessions())
            elif path == "/api/models":
                self._handle_models_list()
            elif path == "/api/training/stats":
                self._handle_training_stats()
            elif path == "/api/training/status":
                self._handle_training_status()
            elif path == "/favicon.ico":
                self._send_text(204, b"", "image/x-icon")
            else:
                self._send_text(404, b"not found", "text/plain")
        except Exception as e:  # noqa: BLE001
            log.exception("GET %s failed", path)
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

    # ── POST routes ──
    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        try:
            data = self._read_json()
        except Exception:
            self._send_json(400, {"error": "invalid JSON"})
            return
        try:
            if path == "/api/chat":
                text = (data.get("text") or "").strip()
                if not text:
                    self._send_json(400, {"error": "empty text"})
                    return
                session_id = data.get("session_id")
                result = _run_task(text, session_id=session_id)
                self._send_json(200, result)
            elif path == "/api/sessions/new":
                self._send_json(200, {"ok": True, "session_id": self._new_session()})
            elif path == "/api/sessions/select":
                sid = data.get("id")
                self._send_json(200, {"ok": True, "selected": sid})
            elif path == "/api/models/use":
                self._handle_models_use(data)
            elif path == "/api/models/pull":
                self._handle_models_pull(data)
            elif path == "/api/models/delete":
                self._handle_models_delete(data)
            elif path == "/api/training/export":
                self._handle_training_export()
            elif path == "/api/training/start":
                self._handle_training_start()
            else:
                self._send_text(404, b"not found", "text/plain")
        except Exception as e:  # noqa: BLE001
            log.exception("POST %s failed", path)
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

    # ── models (Local Brain) ──
    def _handle_models_list(self) -> None:
        from odc import Config
        from odc.llm.local import LocalBrain
        try:
            brain = LocalBrain(Config())
            data = brain.list_models()
            self._send_json(200, {"models": data})
        except Exception as e:
            self._send_json(200, {"models": [], "error": str(e), "hint": "Install Ollama: https://ollama.com"})

    def _handle_models_use(self, data: dict) -> None:
        from odc import Config
        from odc.llm.local import LocalBrain
        name = data.get("name")
        if not name:
            self._send_json(400, {"error": "name required"})
            return
        LocalBrain(Config()).set_active(name)
        self._send_json(200, {"ok": True, "active": name})

    def _handle_models_pull(self, data: dict) -> None:
        from odc import Config
        from odc.llm.local import LocalBrain
        name = data.get("name")
        if not name:
            self._send_json(400, {"error": "name required"})
            return
        result = LocalBrain(Config()).pull(name)
        self._send_json(200, result)

    def _handle_models_delete(self, data: dict) -> None:
        from odc import Config
        from odc.llm.local import LocalBrain
        name = data.get("name")
        if not name:
            self._send_json(400, {"error": "name required"})
            return
        result = LocalBrain(Config()).delete(name)
        self._send_json(200, result)

    # ── training ──
    def _handle_training_stats(self) -> None:
        from odc import Config
        from odc.training import TrainingStore
        try:
            store = TrainingStore(Config())
            self._send_json(200, store.stats())
        except Exception as e:
            self._send_json(200, {"error": str(e), "total": 0, "passed": 0, "bipolar": 0})

    def _handle_training_status(self) -> None:
        from odc import Config
        from odc.training import TrainingStore
        try:
            store = TrainingStore(Config())
            self._send_json(200, store.last_run())
        except Exception:
            self._send_json(200, {"status": "never run"})

    def _handle_training_export(self) -> None:
        from odc import Config
        from odc.training import TrainingStore
        try:
            store = TrainingStore(Config())
            result = store.export_dataset()
            self._send_json(200, result)
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_training_start(self) -> None:
        from odc import Config
        from odc.training import TrainingStore
        try:
            store = TrainingStore(Config())
            result = store.start_finetune()
            self._send_json(200, result)
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    # ── sessions ──
    def _new_session(self) -> str:
        try:
            from odc import Config
            from odc.mcp.osiris import OsirisMemory
            cfg = Config()
            mem = OsirisMemory(cfg.data_dir / "memory" / "osiris.db")
            sid = mem.create_session(title="web-session")
            return sid
        except Exception as e:
            log.warning("new session failed: %s", e)
            return ""


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the web server. Blocks."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"ODC v4 web: http://{host}:{port}")
    print("Premium 3D interface (DNA Digital theme).")
    print("Endpoints: /api/info /api/chat /api/models /api/training /api/sessions /api/metrics")
    print("Press Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.shutdown()


def main() -> None:
    p = argparse.ArgumentParser(prog="odc web", description="Web chat for the ODC agent (3D DNA Digital UI).")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
