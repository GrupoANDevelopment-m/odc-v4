"""Web interface for the ODC agent — premium 3D immersive UI.

Serves the dark/glassmorphic interface with a Three.js DNA-helix
background, exposed under /.

Endpoints:
  GET  /                          — main HTML (3D UI)
  GET  /api/info                  — provider/model/tools/skills
  GET  /api/metrics               — live session metrics
  POST /api/chat                  — run a turn (async, returns report)
  GET  /api/sessions              — list sessions
  POST /api/sessions/new          — start new session
  POST /api/sessions/select       — switch session
  GET  /api/models                — list local Ollama models
  POST /api/models/use            — set active local model
  POST /api/models/pull           — pull a new model
  POST /api/models/delete         — remove a model
  GET  /api/training/stats        — dataset stats
  POST /api/training/export       — export curated dataset
  POST /api/training/start        — start fine-tune (Modelfile gen)
  GET  /api/training/status       — last fine-tune status
  POST /api/specialize/run        — full specialization wizard
  POST /api/specialize/preview    — preview constitutional gate
  GET  /api/system/config         — load workspace/web/brain/sandbox
  POST /api/system/config         — save workspace/web/brain/sandbox
  DELETE /api/system/config       — reset to defaults
  POST /api/system/install-ollama — one-click Ollama install
  POST /api/system/install-model  — one-click model install

Run with:
    odc web                 # http://127.0.0.1:8765
    odc web --port 9000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from odc.observability import get_logger, setup_logging

log = get_logger("odc.web")

_HERE = Path(__file__).resolve().parent
INDEX_HTML = (_HERE / "index.html").read_text(encoding="utf-8")

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
    from odc import Config
    from odc.observability.ops import OpsStore

    cfg = Config()
    ops_path = cfg.data_dir / "ops" / "metrics.db"
    summary: dict[str, Any] = {}
    if ops_path.exists():
        try:
            summary = OpsStore(ops_path).summary()
        except Exception:
            pass

    brain = {"name": "primary", "detail": cfg.llm_provider}
    try:
        from odc.llm.local import LocalBrain
        s = LocalBrain(cfg).status()
        if s.get("reachable"):
            brain = {"name": s.get("active") or "local-ready",
                     "detail": f"{s['installed']} model(s) at {s['base_url']}"}
        else:
            brain = {"name": "primary", "detail": f"{cfg.llm_provider} (local brain offline)"}
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


# ── system config persistence ──────────────────────────────────
def _config_path():
    from odc import Config
    return Path(getattr(Config(), "data_dir", "./data")) / "config.json"


DEFAULT_CONFIG = {
    "workspace": {
        "allowed_roots": [],
        "deny_patterns": [".pem", "id_rsa", ".env"],
        "max_file_size_mb": 50,
    },
    "web": {
        "allowed_domains": [],
        "denied_domains": [],
        "allowed_schemes": ["http", "https"],
        "deny_private_ips": True,
    },
    "brain": {
        "base_url": "http://127.0.0.1:11434",
        "auto_start": False,
    },
    "sandbox": {
        "enabled": False,
        "runner": "subprocess",
        "cpu_seconds": 30,
        "memory_mb": 256,
    },
    # Auto-extension: allow the agent to build its own tools/skills
    # at runtime via dynamic.tool_create / dynamic.skill_create.
    # ON by default — the SYSTEM_PROMPT tells the agent to extend
    # itself when a needed capability is missing. User can toggle OFF.
    "auto_extend": {
        "tool_create": True,    # dynamic.tool_create (build new tools)
        "skill_create": True,   # dynamic.skill_create (build new skills)
        "tool_repair": True,    # dynamic.tool_repair (fix broken tools)
        "tool_load": True,      # dynamic.tool_load (load saved tool from disk)
        "require_safety": True, # AST-based safety check before applying
    },
    # Sensory panel: live OSINT sensors
    "osint": {
        "enabled": True,
        "auto_inject": True,   # include OSINT tools in prompts proactively
        "endpoints_no_key": ["flights", "earthquakes", "cve", "bitcoin",
                              "crypto_prices", "space_weather",
                              "weather", "wikipedia"],
        "endpoints_keyed": ["sanctions", "satellites", "fires",
                              "eonet", "news"],
    },
    # Reflexion: persistent learning loop
    "reflexion": {
        "enabled": True,
        "store_outcomes": True,
        "wisdom_not_trauma": True,  # only patterns past exhaustion gate
    },
    # Refinement: Level 9 self-modification
    "refinement": {
        "enabled": True,
        "exhaustion_gate": True,
        "constitutional_guard": True,
        "sandbox_verify": True,
        "min_samples_per_pattern": 5,
        "bipolar_ratio": 0.15,
    },
    # MCP persistent memory substrate
    "memory": {
        "backend": "sqlite",  # or "postgres" in future
        "retention_days": 365,
        "cross_session_recall": False,  # conversation isolation default
        "explicit_recall_only": True,
    },
    # Local brain (secondary LLM)
    "local_brain": {
        "enabled": False,  # user must enable after install
        "provider": "ollama",
        "model": "",
        "auto_switch": False,  # when set, route tool calls through local brain
    },
}


def _load_system_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return json.loads(json.dumps(DEFAULT_CONFIG))


def _save_system_config(cfg: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _reset_system_config() -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    _save_system_config(cfg)
    return cfg


# ── install helpers ─────────────────────────────────────────────
def _detect_platform() -> str:
    s = platform.system().lower()
    if s == "linux": return "linux"
    if s == "darwin": return "macos"
    return s


def _install_ollama_command() -> list[str]:
    """Return the platform-specific command to install Ollama."""
    plat = _detect_platform()
    if plat == "linux":
        # Official installer
        return ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
    if plat == "macos":
        # Try brew first, then download
        if shutil.which("brew"):
            return ["brew", "install", "ollama"]
        return ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
    # Windows — out of scope; return empty to signal not supported
    return []


def _install_ollama(timeout: int = 600) -> dict[str, Any]:
    """Run the platform-appropriate install command. Returns dict."""
    cmd = _install_ollama_command()
    if not cmd:
        return {"ok": False, "error": f"unsupported platform: {_detect_platform()}"}
    log.info("install-ollama: running %s", cmd)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": (proc.stdout or "")[-4000:],
            "stderr": (proc.stderr or "")[-4000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"install timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _pull_model_via_ollama(name: str, timeout: int = 1800) -> dict[str, Any]:
    """Pull a model using the `ollama` CLI as fallback."""
    binary = shutil.which("ollama")
    if not binary:
        return {"ok": False, "error": "ollama CLI not found in PATH"}
    try:
        proc = subprocess.run(
            [binary, "pull", name],
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": (proc.stdout or "")[-4000:],
            "stderr": (proc.stderr or "")[-4000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"pull timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── HTTP handler ────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    """Single handler — routes by path."""

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

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
            elif path == "/api/auto-extend/status":
                cfg = _load_system_config()
                self._send_json(200, cfg.get("auto_extend", {}))
            elif path == "/api/training/status":
                self._handle_training_status()
            elif path == "/api/system/config":
                self._send_json(200, _load_system_config())
            elif path == "/favicon.ico":
                self._send_text(204, b"", "image/x-icon")
            else:
                self._send_text(404, b"not found", "text/plain")
        except Exception as e:
            log.exception("GET %s failed", path)
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

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
            elif path == "/api/specialize/run":
                self._handle_specialize_run(data)
            elif path == "/api/specialize/preview":
                self._handle_specialize_preview(data)
            elif path == "/api/system/config":
                _save_system_config(data)
                # Sync auto-extend gate to the agent module so it applies
                # immediately without restart
                try:
                    from odc.code.auto_extend import (
                        all_gates, save_to_config, load_from_config
                    )
                    ae = data.get("auto_extend", {})
                    # Load from the just-saved config (re-loads gate state)
                    from pathlib import Path
                    load_from_config(_config_path())
                except Exception as e:
                    log.warning("auto_extend sync failed: %s", e)
                self._send_json(200, {"ok": True, "saved": data})
            elif path == "/api/auto-extend/toggle":
                # Toggle one flag in auto_extend
                cfg = _load_system_config()
                ae = cfg.setdefault("auto_extend", {})
                key = data.get("key")
                value = data.get("value")
                if key not in ae:
                    self._send_json(400, {"error": f"unknown key: {key}",
                                           "available": list(ae.keys())})
                    return
                # require_safety cannot be turned off (system invariant)
                if key == "require_safety" and not value:
                    self._send_json(400, {"error": "require_safety is a system invariant and cannot be disabled"})
                    return
                ae[key] = bool(value)
                cfg["auto_extend"] = ae
                _save_system_config(cfg)
                # Sync the in-memory gate module
                try:
                    from odc.code.auto_extend import load_from_config
                    load_from_config(_config_path())
                except Exception:
                    pass
                self._send_json(200, {"ok": True, "auto_extend": ae})
            elif path == "/api/auto-extend/enable-all":
                cfg = _load_system_config()
                ae = cfg.setdefault("auto_extend", {})
                for k in ae:
                    ae[k] = True
                cfg["auto_extend"] = ae
                _save_system_config(cfg)
                try:
                    from odc.code.auto_extend import load_from_config
                    load_from_config(_config_path())
                except Exception:
                    pass
                self._send_json(200, {"ok": True, "auto_extend": ae})
            elif path == "/api/auto-extend/disable-all":
                cfg = _load_system_config()
                ae = cfg.setdefault("auto_extend", {})
                # Never disable safety check — that's a system invariant
                for k in ae:
                    if k != "require_safety":
                        ae[k] = False
                cfg["auto_extend"] = ae
                _save_system_config(cfg)
                try:
                    from odc.code.auto_extend import load_from_config
                    load_from_config(_config_path())
                except Exception:
                    pass
                self._send_json(200, {"ok": True, "auto_extend": ae,
                                       "note": "require_safety kept ON (system invariant)"})
            elif path == "/api/system/install-ollama":
                self._send_json(200, _install_ollama())
            elif path == "/api/system/install-model":
                name = data.get("name", "").strip()
                if not name:
                    self._send_json(400, {"error": "name required"})
                    return
                self._send_json(200, _pull_model_via_ollama(name))
            else:
                self._send_text(404, b"not found", "text/plain")
        except Exception as e:
            log.exception("POST %s failed", path)
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_DELETE(self) -> None:
        path = self.path.split("?")[0]
        try:
            if path == "/api/system/config":
                cfg = _reset_system_config()
                self._send_json(200, {"ok": True, "reset": cfg})
            else:
                self._send_text(404, b"not found", "text/plain")
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    # ── models ──
    def _handle_models_list(self) -> None:
        from odc import Config
        from odc.llm.local import LocalBrain, get_recommended
        cfg = Config()
        brain = LocalBrain(cfg)
        models = brain.list_models()
        active = brain.get_active()
        # If config has a base_url override, use it
        syscfg = _load_system_config()
        if syscfg.get("brain", {}).get("base_url"):
            brain_custom = LocalBrain(cfg, base_url=syscfg["brain"]["base_url"])
            models = brain_custom.list_models()
        self._send_json(200, {
            "models": models,
            "recommended": get_recommended(),
            "active": active,
            "base_url": syscfg.get("brain", {}).get("base_url", "http://127.0.0.1:11434"),
        })

    def _handle_models_use(self, data: dict) -> None:
        from odc import Config
        from odc.llm.local import LocalBrain
        name = data.get("name", "")
        if not name:
            self._send_json(400, {"error": "name required"})
            return
        r = LocalBrain(Config()).set_active(name)
        self._send_json(200, r)

    def _handle_models_pull(self, data: dict) -> None:
        from odc import Config
        from odc.llm.local import LocalBrain
        name = data.get("name", "")
        if not name:
            self._send_json(400, {"error": "name required"})
            return
        r = LocalBrain(Config()).pull(name)
        self._send_json(200, r)

    def _handle_models_delete(self, data: dict) -> None:
        from odc import Config
        from odc.llm.local import LocalBrain
        name = data.get("name", "")
        if not name:
            self._send_json(400, {"error": "name required"})
            return
        r = LocalBrain(Config()).delete(name)
        self._send_json(200, r)

    # ── training ──
    def _handle_training_stats(self) -> None:
        from odc import Config
        from odc.training import TrainingStore
        try:
            store = TrainingStore(Config())
            stats = store.stats()
            stats["last_run"] = store.last_run()
            self._send_json(200, stats)
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
            self._send_json(200, store.export_dataset())
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _handle_training_start(self) -> None:
        from odc import Config
        from odc.training import TrainingStore
        try:
            store = TrainingStore(Config())
            self._send_json(200, store.start_finetune())
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    # ── specialize ──
    def _handle_specialize_run(self, data: dict) -> None:
        from odc import Config
        from odc.training import TrainingStore

        base_model = (data.get("base_model") or "").strip()
        domain = (data.get("domain") or "").strip()
        requirements = (data.get("requirements") or "").strip()
        if not base_model:
            self._send_json(400, {"error": "base_model required"})
            return
        if not domain:
            self._send_json(400, {"error": "domain required"})
            return

        cfg = Config()
        store = TrainingStore(cfg)
        # 1. Export curated dataset
        export_result = store.export_dataset()
        # 2. Set the active base model
        try:
            from odc.llm.local import LocalBrain
            LocalBrain(cfg).set_active(base_model)
        except Exception as e:
            log.warning("set_active failed: %s", e)
        # 3. Generate Modelfile with specialization instructions
        from odc.training.store import TrainingStore as TS
        # Patch the Modelfile with specialization context
        modelfile = cfg.data_dir / "training" / "Modelfile"
        modelfile.parent.mkdir(parents=True, exist_ok=True)
        spec_text = (
            f"# Specialization for domain: {domain}\n"
            f"# Requirements: {requirements}\n"
            f"# Base model: {base_model}\n"
            f"# Generated: {_now()}\n\n"
            f"FROM {base_model}\n\n"
            'SYSTEM """You are a specialized assistant for the domain: ' + domain + '.\n'
            f'Your tasks: {requirements}\n'
            'Reason step by step. Cite evidence. Never claim certainty beyond '
            'your evidence tier. Use the constitutional framing provided by ODC."""\n'
        )
        modelfile.write_text(spec_text, encoding="utf-8")
        # 4. Mark fine-tune as ready
        run = store.start_finetune()
        self._send_json(200, {
            "ok": True,
            "export": export_result,
            "modelfile": str(modelfile),
            "specialization": {
                "base_model": base_model,
                "domain": domain,
                "requirements": requirements,
            },
            "next_step": (
                f"Run: ollama create odc-{domain} -f {modelfile}\n"
                "Then: ollama run odc-" + domain
            ),
        })

    def _handle_specialize_preview(self, data: dict) -> None:
        from odc import Config
        from odc.training import TrainingStore
        cfg = Config()
        store = TrainingStore(cfg)
        # Show what would be kept / dropped without writing
        from odc.training.curator import DatasetCurator
        c = DatasetCurator(cfg.data_dir)
        raw = c._read_field_outcomes()
        audit = c._read_audit_trail()
        sandbox = c._read_sandbox_log()
        self._send_json(200, {
            "raw_field_outcomes": len(raw),
            "raw_audit_rows": len(audit),
            "raw_sandbox_results": len(sandbox),
            "constitutional_tiers_accepted": ["AUTHORITATIVE_API", "DIRECT_OBSERVATION", "CORROBORATED"],
            "bipolar_threshold": "minority/total >= 15%",
            "min_samples_per_pattern": 5,
            "scrub_patterns": [
                "OpenAI keys (sk-...)", "NVIDIA keys (nvapi-...)",
                "GitHub tokens (ghp_...)", "AWS access keys (AKIA...)",
                "PEM private keys", "SSN", "credit cards",
                "emails", "absolute paths",
            ],
        })

    # ── sessions ──
    def _new_session(self) -> str:
        try:
            from odc import Config
            from odc.mcp.osiris import OsirisMemory
            cfg = Config()
            mem = OsirisMemory(cfg.data_dir / "memory" / "osiris.db")
            return mem.create_session(title="web-session")
        except Exception as e:
            log.warning("new session failed: %s", e)
            return ""


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"ODC v4 web: http://{host}:{port}")
    print("Premium 3D interface (DNA Digital theme).")
    print("Subpages: / Chat | / Brain | / Training | / Specialize | / Settings")
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
