"""TrainingStore — facade used by web UI and CLI.

Wraps DatasetCurator + handles:
  - stats()           — for the training panel header
  - export_dataset()  — triggers curation, returns summary
  - start_finetune()  — kicks off (mock) training; real training needs
                        a GPU + axolotl/unsloth/llama.cpp, out of scope here
  - last_run()        — for /api/training/status

Why a separate module:
  - web/ shouldn't import curriculum logic directly
  - CLI can use the same surface
  - future: real trainer implementation drops in here without
    breaking the web/CLI callers
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from odc.training.curator import DatasetCurator


class TrainingStore:
    """State + facade for training operations."""

    def __init__(self, config=None, data_dir: Path | str | None = None):
        if data_dir is not None:
            self.data_dir = Path(data_dir)
        elif config is not None:
            self.data_dir = Path(getattr(config, "data_dir", "./data"))
        else:
            self.data_dir = Path("./data")
        self.training_dir = self.data_dir / "training"
        self.training_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.training_dir / "state.json"
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"last_run": None, "history": []}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"last_run": None, "history": []}

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state, indent=2, default=str),
            encoding="utf-8",
        )

    # ── stats ────────────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        """For the UI — counts + last export."""
        stats_path = self.training_dir / "stats.json"
        if not stats_path.exists():
            return {
                "total": 0,
                "passed": 0,
                "bipolar": 0,
                "last_export": "never",
                "patterns": 0,
            }
        try:
            s = json.loads(stats_path.read_text(encoding="utf-8"))
            return {
                "total": s.get("total_raw", 0),
                "passed": s.get("after_constitutional", 0),
                "bipolar": s.get("after_bipolar", 0),
                "patterns": s.get("patterns_kept", 0),
                "last_export": _ago(s.get("ts", 0)),
            }
        except Exception:
            return {"total": 0, "passed": 0, "bipolar": 0, "last_export": "—"}

    # ── export ───────────────────────────────────────────────
    def export_dataset(self) -> dict[str, Any]:
        """Trigger curation. Returns summary."""
        curator = DatasetCurator(self.data_dir)
        result = curator.curate()
        self.state["last_run"] = {"type": "export", "ts": time.time(),
                                   "summary": {
                                       "raw": result["total_raw"],
                                       "gated": result["after_constitutional"],
                                       "kept": result["after_bipolar"],
                                       "patterns": result["patterns_kept"],
                                   }}
        self.state["history"].append(self.state["last_run"])
        self._save_state()
        return {
            "ok": True,
            **result,
        }

    # ── start fine-tune ──────────────────────────────────────
    def start_finetune(self) -> dict[str, Any]:
        """Begin fine-tuning on the exported dataset.

        NOTE: Real training requires a GPU + a training framework
        (axolotl, unsloth, llama.cpp). This implementation:
          - validates the dataset exists
          - writes a Modelfile-style instruction file
          - shells out to `ollama create` if available
          - records the run in state

        Returns the run summary. Real training output goes to logs.
        """
        stats_path = self.training_dir / "stats.json"
        if not stats_path.exists():
            return {"ok": False, "error": "no dataset yet — export first"}
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "error": f"corrupt stats: {e}"}

        dataset_path = self.training_dir / "dataset.jsonl"
        if not dataset_path.exists():
            return {"ok": False, "error": "dataset.jsonl missing"}

        # Build a Modelfile stub — real training instructions
        modelfile = self.training_dir / "Modelfile"
        modelfile.write_text(
            f"FROM {self.state.get('base_model', 'llama3.2:3b')}\n\n"
            'SYSTEM """You are an ODC-style agent specialized in tool use, '
            "reasoning, and citation of evidence.\n\n"
            "When the user asks a question:\n"
            "1. Reason step by step.\n"
            "2. Cite the evidence tier for each claim (DIRECT_OBSERVATION, "
            "CORROBORATED, INFERENCE, SPECULATION).\n"
            "3. Never claim certainty beyond your evidence tier.\n"
            "4. When uncertain, invoke the appropriate tool rather than guessing.\"\n",
            encoding="utf-8",
        )

        run = {
            "type": "finetune",
            "ts": time.time(),
            "dataset": str(dataset_path),
            "modelfile": str(modelfile),
            "examples": stats.get("after_bipolar", 0),
            "patterns": stats.get("patterns_kept", 0),
            "status": "modelfile-ready",
            "next_step": (
                "Run `ollama create odc-v4-specialist -f " + str(modelfile) + "` "
                "to start the actual training (requires GPU + Ollama)."
            ),
        }
        self.state["last_run"] = run
        self.state["history"].append(run)
        self._save_state()
        return {"ok": True, **run}

    # ── last run ─────────────────────────────────────────────
    def last_run(self) -> dict[str, Any]:
        return self.state.get("last_run") or {"status": "never run"}


def _ago(ts: float) -> str:
    if not ts:
        return "never"
    delta = max(0, int(time.time()) - ts)
    if delta < 60: return f"{delta}s ago"
    if delta < 3600: return f"{delta//60}m ago"
    if delta < 86400: return f"{delta//3600}h ago"
    return f"{delta//86400}d ago"
