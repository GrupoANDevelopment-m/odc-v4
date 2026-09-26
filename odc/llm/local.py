"""Local Brain — local LLM management (Ollama-compatible).

What this does:
  - Talks to a local Ollama server (or any Ollama-API-compatible server).
  - Lists installed models (GET /api/tags).
  - Pulls new models (POST /api/pull) with progress tracking.
  - Deletes models (DELETE /api/delete).
  - Tracks the *active* secondary model per workspace.
  - Persists the active model + install history in <data_dir>/brain/.
  - Reports status to the web UI /api/models endpoint.

Design notes:
  - All HTTP calls go through stdlib (urllib) — zero extra deps.
  - No subprocess. We treat the Ollama daemon as a separate process
    the user runs; we don't try to install Ollama ourselves (different
    per-OS concerns).
  - If Ollama isn't running, every call returns a structured error;
    callers (UI, agent) handle gracefully.
  - The local model is a *secondary brain* — primary reasoning still
    uses whatever provider is configured (Anthropic / OpenAI / etc.).
    The agent routes through both via the LocalBrainRouter.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = os.environ.get("ODC_OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_TIMEOUT_S = 5.0
PULL_TIMEOUT_S = 1800.0  # 30 min for large pulls


@dataclass
class ModelInfo:
    """Single installed model."""
    name: str
    size: int = 0              # bytes
    family: str = ""
    parameter_size: str = ""
    quantization_level: str = ""
    modified_at: str = ""
    digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "family": self.family,
            "parameter_size": self.parameter_size,
            "quantization_level": self.quantization_level,
            "modified_at": self.modified_at,
            "digest": self.digest,
        }


@dataclass
class BrainState:
    """Persisted state — what's installed, what's active."""
    active_model: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    last_check: float = 0.0
    reachable: bool = False


def _http_json(url: str, payload: dict | None = None,
               method: str = "GET", timeout: float = DEFAULT_TIMEOUT_S,
               stream: bool = False) -> dict[str, Any]:
    """Issue a JSON HTTP request via stdlib."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"Ollama HTTP {e.code}: {body or e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama unreachable at {url}: {e.reason}") from e
    except (socket.timeout, TimeoutError) as e:
        raise RuntimeError(f"Ollama timeout after {timeout}s") from e


def _http_streaming_jsonl(url: str, payload: dict, timeout: float = PULL_TIMEOUT_S):
    """Stream newline-delimited JSON from an HTTP endpoint.

    Yields parsed dicts. Used for /api/pull which streams progress.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw_line in resp:
            line = raw_line.strip()
            if not line:
                continue
            try:
                yield json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                yield {"raw": line.decode("utf-8", errors="replace")}


def probe(base_url: str = DEFAULT_BASE_URL, timeout: float = 2.0) -> bool:
    """Quick check — is the Ollama daemon reachable? Don't raise."""
    try:
        _http_json(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        return True
    except Exception:
        return False


class LocalBrain:
    """Manage local Ollama models.

    Persists state at <data_dir>/brain/state.json.
    """

    def __init__(self, config=None, base_url: str | None = None, data_dir: Path | str | None = None):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.config = config
        if data_dir is not None:
            self.data_dir = Path(data_dir) / "brain"
        elif config is not None:
            self.data_dir = Path(getattr(config, "data_dir", "./data")) / "brain"
        else:
            self.data_dir = Path("./data/brain")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "state.json"
        self.state = self._load_state()

    # ── state persistence ────────────────────────────────────
    def _load_state(self) -> BrainState:
        if not self.state_path.exists():
            return BrainState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return BrainState(
                active_model=data.get("active_model", ""),
                history=data.get("history", []),
                last_check=data.get("last_check", 0.0),
                reachable=data.get("reachable", False),
            )
        except Exception:
            return BrainState()

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(
                {
                    "active_model": self.state.active_model,
                    "history": self.state.history[-50:],  # cap
                    "last_check": self.state.last_check,
                    "reachable": self.state.reachable,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # ── reachability ────────────────────────────────────────
    def is_reachable(self) -> bool:
        """Quick probe. Updates state.reachable."""
        ok = probe(self.base_url)
        self.state.reachable = ok
        self.state.last_check = time.time()
        self._save_state()
        return ok

    # ── list ────────────────────────────────────────────────
    def list_models(self) -> list[dict[str, Any]]:
        """Return installed models as list of dicts. Empty list on failure."""
        if not self.is_reachable():
            return []
        try:
            data = _http_json(f"{self.base_url}/api/tags")
            models = []
            for m in data.get("models", []):
                info = ModelInfo(
                    name=m.get("name", ""),
                    size=m.get("size", 0),
                    family=(m.get("details") or {}).get("family", ""),
                    parameter_size=(m.get("details") or {}).get("parameter_size", ""),
                    quantization_level=(m.get("details") or {}).get("quantization_level", ""),
                    modified_at=m.get("modified_at", ""),
                    digest=m.get("digest", ""),
                )
                models.append(info.to_dict())
            return models
        except Exception:
            return []

    # ── pull ────────────────────────────────────────────────
    def pull(self, name: str, callback=None) -> dict[str, Any]:
        """Pull a model. Streams progress; optional callback(dict).

        callback receives each progress event dict from Ollama:
          {"status": "pulling manifest", ...}
          {"status": "downloading", "completed": n, "total": m, ...}
          {"status": "verifying sha256"}
          {"status": "success"}
        """
        if not name or not isinstance(name, str):
            return {"ok": False, "error": "invalid name"}

        # Validate name shape to prevent injection
        if not all(c.isalnum() or c in "._:-/" for c in name):
            return {"ok": False, "error": f"invalid model name: {name!r}"}

        if not self.is_reachable():
            return {
                "ok": False,
                "error": f"Ollama not reachable at {self.base_url}",
                "hint": "Install: https://ollama.com — then `ollama serve`",
            }

        events: list[dict] = []
        try:
            for ev in _http_streaming_jsonl(
                f"{self.base_url}/api/pull",
                {"name": name, "stream": True},
            ):
                events.append(ev)
                if callback:
                    try:
                        callback(ev)
                    except Exception:
                        pass
                if ev.get("status") == "success":
                    break
                if "error" in ev:
                    return {
                        "ok": False,
                        "error": ev.get("error"),
                        "events": events[-5:],
                    }
        except Exception as e:
            return {"ok": False, "error": str(e), "events": events[-5:]}

        # record
        self.state.history.append({
            "action": "pull",
            "name": name,
            "ts": time.time(),
            "ok": True,
        })
        self._save_state()
        return {"ok": True, "model": name, "events": len(events)}

    # ── delete ──────────────────────────────────────────────
    def delete(self, name: str) -> dict[str, Any]:
        """Remove a model. Ollama returns 200 on success."""
        if not name:
            return {"ok": False, "error": "invalid name"}
        if not all(c.isalnum() or c in "._:-/" for c in name):
            return {"ok": False, "error": "invalid name chars"}
        if not self.is_reachable():
            return {"ok": False, "error": "Ollama not reachable"}
        try:
            _http_json(
                f"{self.base_url}/api/delete",
                {"name": name},
                method="DELETE",
            )
            if self.state.active_model == name:
                self.state.active_model = ""
            self.state.history.append({
                "action": "delete",
                "name": name,
                "ts": time.time(),
                "ok": True,
            })
            self._save_state()
            return {"ok": True, "model": name}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── active model management ─────────────────────────────
    def set_active(self, name: str) -> dict[str, Any]:
        """Mark a local model as the active secondary brain."""
        if name and not all(c.isalnum() or c in "._:-/" for c in name):
            return {"ok": False, "error": "invalid name chars"}
        # Verify it exists
        installed = {m["name"] for m in self.list_models()}
        if name and installed and name not in installed:
            return {"ok": False, "error": f"model {name!r} not installed",
                    "installed": sorted(installed)}
        self.state.active_model = name
        self._save_state()
        return {"ok": True, "active": name}

    def get_active(self) -> str:
        return self.state.active_model

    # ── status (for /api/metrics) ────────────────────────────
    def status(self) -> dict[str, Any]:
        """Quick status snapshot."""
        reachable = self.is_reachable()
        installed = self.list_models() if reachable else []
        return {
            "reachable": reachable,
            "base_url": self.base_url,
            "installed": len(installed),
            "active": self.state.active_model,
            "last_check": self.state.last_check,
            "history_count": len(self.state.history),
        }


# ── Recommended starter models ─────────────────────────────────
RECOMMENDED_MODELS = [
    {
        "name": "llama3.2:3b",
        "size_gb": 2.0,
        "description": "Meta Llama 3.2 — smallest, runs on 4GB RAM. Good generalist.",
        "tag": "starter",
    },
    {
        "name": "phi3:mini",
        "size_gb": 2.3,
        "description": "Microsoft Phi-3 — strong reasoning for size. Fast on CPU.",
        "tag": "starter",
    },
    {
        "name": "qwen2.5:7b",
        "size_gb": 4.7,
        "description": "Qwen 2.5 7B — strong multilingual + tool use. Needs 8GB RAM.",
        "tag": "balanced",
    },
    {
        "name": "mistral-nemo:12b",
        "size_gb": 7.0,
        "description": "Mistral Nemo — strong on reasoning. Needs 16GB RAM.",
        "tag": "balanced",
    },
    {
        "name": "llama3.3:70b",
        "size_gb": 43.0,
        "description": "Llama 3.3 70B — near-frontier quality. Needs 48GB+ GPU.",
        "tag": "frontier",
    },
]


def get_recommended() -> list[dict[str, Any]]:
    return list(RECOMMENDED_MODELS)
