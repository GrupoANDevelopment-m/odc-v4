"""LocalBrain tests — no live Ollama needed (mocked HTTP)."""
import json
import socket
from unittest.mock import patch

import pytest

from odc.llm.local import (
    LocalBrain, ModelInfo, BrainState, probe,
    _http_json, _http_streaming_jsonl,
    get_recommended, DEFAULT_BASE_URL,
    RECOMMENDED_MODELS,
)


# ── helpers ─────────────────────────────────────────────────────
class FakeHTTPResponse:
    def __init__(self, body=b"", lines=None, code=200):
        self._body = body
        self._lines = lines or []
        self.code = code
    def read(self):
        return self._body
    def __iter__(self):
        return iter(self._lines)


# ── probe / reachability ────────────────────────────────────────
class TestProbe:
    def test_probe_returns_false_when_unreachable(self):
        # Point to a port nothing listens on
        ok = probe("http://127.0.0.1:1", timeout=0.5)
        assert ok is False

    def test_probe_swallows_errors(self):
        # Should never raise, even if URL is malformed
        assert probe("http://invalid.example.local:9999", timeout=0.1) is False


# ── state persistence ──────────────────────────────────────────
class TestStatePersistence:
    def test_state_path_under_data_dir(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        assert b.data_dir == tmp_path / "brain"
        assert b.state_path.parent.exists()

    def test_state_roundtrip(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        b.state.active_model = "llama3.2:3b"
        b.state.history.append({"action": "pull", "name": "llama3.2:3b"})
        b._save_state()

        b2 = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        assert b2.state.active_model == "llama3.2:3b"
        assert len(b2.state.history) == 1

    def test_corrupt_state_recovers(self, tmp_path):
        brain_dir = tmp_path / "brain"
        brain_dir.mkdir()
        (brain_dir / "state.json").write_text("not json{", encoding="utf-8")
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        assert b.state.active_model == ""

    def test_history_capped_at_50(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        for i in range(80):
            b.state.history.append({"i": i})
        b._save_state()
        b2 = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        assert len(b2.state.history) == 50


# ── list / mock HTTP ────────────────────────────────────────────
class TestListModels:
    def test_list_models_returns_empty_when_unreachable(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path, base_url="http://127.0.0.1:1")  # type: ignore[arg-type]
        assert b.list_models() == []

    def test_list_models_parses_response(self, tmp_path):
        body = json.dumps({
            "models": [
                {"name": "llama3.2:3b", "size": 2_000_000_000,
                 "details": {"family": "llama", "parameter_size": "3B",
                             "quantization_level": "Q4_0"},
                 "modified_at": "2026-09-01T00:00:00Z",
                 "digest": "sha256:abc"},
            ]
        }).encode("utf-8")

        b = LocalBrain(data_dir=tmp_path, base_url="http://mock")  # type: ignore[arg-type]
        with patch.object(b, "is_reachable", return_value=True), \
             patch("odc.llm.local._http_json", return_value=json.loads(body)):
            models = b.list_models()

        assert len(models) == 1
        m = models[0]
        assert m["name"] == "llama3.2:3b"
        assert m["size"] == 2_000_000_000
        assert m["family"] == "llama"
        assert m["parameter_size"] == "3B"


# ── pull ────────────────────────────────────────────────────────
class TestPull:
    def test_pull_rejects_invalid_name(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        for bad in ["", "rm -rf /", "foo bar", "name with spaces"]:
            r = b.pull(bad)
            assert r["ok"] is False

    def test_pull_reports_unreachable(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path, base_url="http://127.0.0.1:1")  # type: ignore[arg-type]
        r = b.pull("llama3.2:3b")
        assert r["ok"] is False
        assert "reachable" in r["error"].lower() or "unreachable" in r["error"].lower()

    def test_pull_streams_and_returns_success(self, tmp_path):
        events = [
            {"status": "pulling manifest"},
            {"status": "downloading", "completed": 100, "total": 1000},
            {"status": "success"},
        ]

        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        with patch.object(b, "is_reachable", return_value=True), \
             patch("odc.llm.local._http_streaming_jsonl",
                   return_value=iter(events)):
            r = b.pull("llama3.2:3b")

        assert r["ok"] is True
        assert r["model"] == "llama3.2:3b"
        assert r["events"] == 3

    def test_pull_records_history(self, tmp_path):
        events = [{"status": "success"}]
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        with patch.object(b, "is_reachable", return_value=True), \
             patch("odc.llm.local._http_streaming_jsonl",
                   return_value=iter(events)):
            b.pull("phi3:mini")
        assert any(h["action"] == "pull" for h in b.state.history)

    def test_pull_callback_invoked(self, tmp_path):
        events = [{"status": "downloading"}, {"status": "success"}]
        captured = []
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        with patch.object(b, "is_reachable", return_value=True), \
             patch("odc.llm.local._http_streaming_jsonl",
                   return_value=iter(events)):
            b.pull("qwen2.5:7b", callback=lambda e: captured.append(e))
        assert len(captured) == 2


# ── delete ──────────────────────────────────────────────────────
class TestDelete:
    def test_delete_rejects_invalid_name(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        assert b.delete("").get("ok") is False
        assert b.delete("foo; rm -rf /").get("ok") is False

    def test_delete_clears_active_if_match(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        b.state.active_model = "llama3.2:3b"
        b._save_state()

        with patch.object(b, "is_reachable", return_value=True), \
             patch("odc.llm.local._http_json", return_value={}):
            r = b.delete("llama3.2:3b")
        assert r["ok"] is True
        assert b.state.active_model == ""


# ── set_active ──────────────────────────────────────────────────
class TestSetActive:
    def test_set_active_validates_name(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        assert b.set_active("rm -rf /").get("ok") is False

    def test_set_active_rejects_uninstalled(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        with patch.object(b, "list_models", return_value=[
            {"name": "llama3.2:3b"},
        ]):
            r = b.set_active("not-installed")
        assert r["ok"] is False
        assert "not installed" in r["error"]

    def test_set_active_succeeds(self, tmp_path):
        b = LocalBrain(data_dir=tmp_path)  # type: ignore[arg-type]
        with patch.object(b, "list_models", return_value=[
            {"name": "llama3.2:3b"},
        ]):
            r = b.set_active("llama3.2:3b")
        assert r["ok"] is True
        assert b.state.active_model == "llama3.2:3b"


# ── recommended ─────────────────────────────────────────────────
class TestRecommended:
    def test_recommended_returns_known_models(self):
        rec = get_recommended()
        names = [m["name"] for m in rec]
        assert "llama3.2:3b" in names
        assert "qwen2.5:7b" in names

    def test_recommended_has_size_info(self):
        rec = get_recommended()
        for m in rec:
            assert "size_gb" in m
            assert "tag" in m
            assert m["size_gb"] > 0


# ── ModelInfo serialization ─────────────────────────────────────
class TestModelInfo:
    def test_to_dict_includes_all_fields(self):
        m = ModelInfo(name="test:1b", size=1000, family="llama",
                       parameter_size="1B", quantization_level="Q4_0")
        d = m.to_dict()
        assert d["name"] == "test:1b"
        assert d["size"] == 1000
        assert d["family"] == "llama"
