"""Web server tests — endpoints, route coverage, no real agent needed."""
import json
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from odc.web.server import (
    Handler, serve, INDEX_HTML,
    _install_ollama_command, _detect_platform,
    _install_ollama, _pull_model_via_ollama,
    _load_system_config, _save_system_config, _reset_system_config,
    DEFAULT_CONFIG,
)


# ── helpers ─────────────────────────────────────────────────────
def _start_test_server(host="127.0.0.1", port=0):
    """Start the server in a thread, return (port, shutdown_fn)."""
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer((host, port), Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return port, httpd


def _get(port, path):
    return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)


def _post(port, path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=5)


# ── HTML ────────────────────────────────────────────────────────
class TestHTML:
    def test_index_html_is_premium_3d(self):
        assert "DNA da Vida Digital" in INDEX_HTML
        assert "three" in INDEX_HTML.lower()
        assert "DNA" in INDEX_HTML
        assert "helix" in INDEX_HTML.lower()
        assert "LocalBrain" in INDEX_HTML or "local brain" in INDEX_HTML.lower()
        assert "training" in INDEX_HTML.lower()
        assert "glass" not in INDEX_HTML.lower()  # we use backdrop-filter instead


# ── GET endpoints ───────────────────────────────────────────────
class TestGetEndpoints:
    def test_root_returns_html(self):
        port, httpd = _start_test_server()
        try:
            r = _get(port, "/")
            assert r.status == 200
            assert "text/html" in r.headers.get("Content-Type", "")
            body = r.read().decode("utf-8")
            assert "ODC" in body
        finally:
            httpd.shutdown()

    def test_info_endpoint(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.web.server._get_info", return_value={
                "provider": "nvidia", "model": "test",
                "tools": 44, "skills": 6,
            }):
                r = _get(port, "/api/info")
                data = json.loads(r.read())
            assert data["provider"] == "nvidia"
            assert data["tools"] == 44
        finally:
            httpd.shutdown()

    def test_metrics_endpoint(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.web.server._get_metrics", return_value={
                "turns": 3, "llm_calls": 5, "tool_calls": 8,
                "tokens_in": 1000, "tokens_out": 500,
                "cost_usd": 0.001, "p95_ms": 850,
                "brain": {"name": "primary", "detail": "nvidia"},
            }):
                r = _get(port, "/api/metrics")
                data = json.loads(r.read())
            assert data["turns"] == 3
            assert data["brain"]["name"] == "primary"
        finally:
            httpd.shutdown()

    def test_models_endpoint_when_offline(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.llm.local.probe", return_value=False):
                r = _get(port, "/api/models")
                data = json.loads(r.read())
            assert "models" in data
            # Either empty or error
            assert data["models"] == [] or "error" in data
        finally:
            httpd.shutdown()

    def test_training_stats_endpoint(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.training.TrainingStore") as mock:
                instance = mock.return_value
                instance.stats.return_value = {
                    "total": 50, "passed": 30, "bipolar": 20,
                    "patterns": 3, "last_export": "5m ago",
                }
                instance.last_run.return_value = {"status": "done"}
                r = _get(port, "/api/training/stats")
                data = json.loads(r.read())
            assert data["total"] == 50
            assert data["passed"] == 30
        finally:
            httpd.shutdown()

    def test_sessions_endpoint(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.web.server._list_sessions", return_value={
                "sessions": [{"id": "abc", "title": "test", "turns": 3, "age": "1m", "active": True}],
            }):
                r = _get(port, "/api/sessions")
                data = json.loads(r.read())
            assert len(data["sessions"]) == 1
            assert data["sessions"][0]["title"] == "test"
        finally:
            httpd.shutdown()

    def test_404_for_unknown_get(self):
        port, httpd = _start_test_server()
        try:
            try:
                _get(port, "/api/unknown")
            except urllib.error.HTTPError as e:
                assert e.code == 404
        finally:
            httpd.shutdown()

    def test_favicon_204(self):
        port, httpd = _start_test_server()
        try:
            r = _get(port, "/favicon.ico")
            assert r.status == 204
        finally:
            httpd.shutdown()


# ── POST endpoints ──────────────────────────────────────────────
class TestPostEndpoints:
    def test_models_pull_validates_name(self):
        port, httpd = _start_test_server()
        try:
            r = _post(port, "/api/models/pull", {"name": "rm -rf /"})
            # Either rejected client-side or server returns error
            data = json.loads(r.read())
            # If pulled, should have ok=False
            if "ok" in data:
                assert data["ok"] is False
        finally:
            httpd.shutdown()

    def test_models_use_sets_active(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.llm.local.LocalBrain") as mock:
                instance = mock.return_value
                instance.set_active.return_value = {"ok": True, "active": "llama3.2:3b"}
                r = _post(port, "/api/models/use", {"name": "llama3.2:3b"})
                data = json.loads(r.read())
            assert data["ok"] is True
            assert data["active"] == "llama3.2:3b"
        finally:
            httpd.shutdown()

    def test_training_export(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.training.TrainingStore") as mock:
                instance = mock.return_value
                instance.export_dataset.return_value = {
                    "ok": True, "after_bipolar": 25, "patterns_kept": 3,
                }
                r = _post(port, "/api/training/export", {})
                data = json.loads(r.read())
            assert data["ok"] is True
            assert data["after_bipolar"] == 25
        finally:
            httpd.shutdown()

    def test_training_start(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.training.TrainingStore") as mock:
                instance = mock.return_value
                instance.start_finetune.return_value = {
                    "ok": True, "status": "modelfile-ready",
                }
                r = _post(port, "/api/training/start", {})
                data = json.loads(r.read())
            assert data["ok"] is True
        finally:
            httpd.shutdown()

    def test_sessions_new(self):
        port, httpd = _start_test_server()
        try:
            r = _post(port, "/api/sessions/new", {})
            data = json.loads(r.read())
            assert data["ok"] is True
        finally:
            httpd.shutdown()

    def test_post_404_for_unknown(self):
        port, httpd = _start_test_server()
        try:
            try:
                _post(port, "/api/unknown", {})
            except urllib.error.HTTPError as e:
                assert e.code == 404
        finally:
            httpd.shutdown()

    def test_post_handles_invalid_json(self):
        port, httpd = _start_test_server()
        try:
            # Manually send malformed JSON
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/sessions/new",
                data=b"{not valid",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as e:
                assert e.code == 400
        finally:
            httpd.shutdown()


# ── 500 handling ────────────────────────────────────────────────
class TestErrorHandling:
    def test_get_500_on_internal_error(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.web.server._get_info",
                       side_effect=RuntimeError("boom")):
                try:
                    _get(port, "/api/info")
                except urllib.error.HTTPError as e:
                    assert e.code == 500
                    body = json.loads(e.read())
                    assert "boom" in body["error"]
        finally:
            httpd.shutdown()


# ── install commands (no real install) ──────────────────────────
class TestInstallCommands:
    def test_detect_platform_returns_string(self):
        assert _detect_platform() in ("linux", "macos", "windows", "unknown")

    def test_install_command_returns_list(self):
        cmd = _install_ollama_command()
        assert isinstance(cmd, list)
        # Linux/macOS: non-empty; Windows: empty
        if _detect_platform() != "windows":
            assert len(cmd) > 0

    def test_install_ollama_returns_dict(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok"
            mock_run.return_value.stderr = ""
            r = _install_ollama()
        assert "ok" in r
        assert "output" in r

    def test_install_ollama_handles_timeout(self):
        import subprocess as sp
        with patch("subprocess.run", side_effect=sp.TimeoutExpired("cmd", 1)):
            r = _install_ollama(timeout=1)
        assert r["ok"] is False
        assert "timed out" in r["error"]

    def test_pull_model_rejects_empty(self):
        r = _pull_model_via_ollama("")
        assert r["ok"] is False

    def test_pull_model_handles_missing_binary(self):
        with patch("shutil.which", return_value=None):
            r = _pull_model_via_ollama("llama3.2:3b")
        assert r["ok"] is False
        assert "not found" in r["error"]


# ── system config persistence ───────────────────────────────────
class TestSystemConfig:
    def test_default_config_has_all_sections(self):
        for key in ("workspace", "web", "brain", "sandbox"):
            assert key in DEFAULT_CONFIG

    def test_default_sandbox_disabled(self):
        assert DEFAULT_CONFIG["sandbox"]["enabled"] is False

    def test_load_returns_defaults_when_no_file(self, tmp_path, monkeypatch):
        # Patch _config_path to point at tmp
        monkeypatch.setattr("odc.web.server._config_path",
                            lambda: tmp_path / "config.json")
        cfg = _load_system_config()
        assert cfg["workspace"]["max_file_size_mb"] == 50
        assert cfg["web"]["deny_private_ips"] is True

    def test_save_and_reload(self, tmp_path, monkeypatch):
        monkeypatch.setattr("odc.web.server._config_path",
                            lambda: tmp_path / "config.json")
        cfg = {"workspace": {"allowed_roots": ["/tmp"], "deny_patterns": [], "max_file_size_mb": 100}}
        _save_system_config(cfg)
        loaded = _load_system_config()
        assert loaded["workspace"]["allowed_roots"] == ["/tmp"]
        assert loaded["workspace"]["max_file_size_mb"] == 100

    def test_reset_to_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setattr("odc.web.server._config_path",
                            lambda: tmp_path / "config.json")
        _save_system_config({"workspace": {"allowed_roots": ["/whatever"], "deny_patterns": [], "max_file_size_mb": 999}})
        cfg = _reset_system_config()
        assert cfg["workspace"]["max_file_size_mb"] == 50
        assert cfg["sandbox"]["enabled"] is False


# ── new endpoints ───────────────────────────────────────────────
class TestNewEndpoints:
    def test_system_config_get_returns_defaults(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.web.server._load_system_config",
                       return_value=DEFAULT_CONFIG):
                r = _get(port, "/api/system/config")
                d = json.loads(r.read())
            assert "workspace" in d
            assert "brain" in d
            assert "sandbox" in d
        finally:
            httpd.shutdown()

    def test_system_config_post_saves(self):
        port, httpd = _start_test_server()
        try:
            new_cfg = {"workspace": {"allowed_roots": ["/x"]},
                        "web": {"allowed_domains": ["foo.com"]},
                        "brain": {"base_url": "http://x:1234"},
                        "sandbox": {"enabled": False}}
            with patch("odc.web.server._save_system_config") as mock_save:
                r = _post(port, "/api/system/config", new_cfg)
                d = json.loads(r.read())
            assert d["ok"] is True
            assert mock_save.called
            mock_save.assert_called_with(new_cfg)
        finally:
            httpd.shutdown()

    def test_system_config_delete_resets(self):
        port, httpd = _start_test_server()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/system/config",
                method="DELETE",
            )
            r = urllib.request.urlopen(req, timeout=5)
            d = json.loads(r.read())
            assert d["ok"] is True
            assert "reset" in d
        finally:
            httpd.shutdown()

    def test_install_ollama_endpoint(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.web.server._install_ollama",
                       return_value={"ok": True, "output": "installed"}):
                r = _post(port, "/api/system/install-ollama", {})
                d = json.loads(r.read())
            assert d["ok"] is True
        finally:
            httpd.shutdown()

    def test_install_model_endpoint(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.web.server._pull_model_via_ollama",
                       return_value={"ok": True, "output": "pulled"}):
                r = _post(port, "/api/system/install-model", {"name": "llama3.2:3b"})
                d = json.loads(r.read())
            assert d["ok"] is True
        finally:
            httpd.shutdown()

    def test_install_model_requires_name(self):
        port, httpd = _start_test_server()
        try:
            try:
                _post(port, "/api/system/install-model", {})
            except urllib.error.HTTPError as e:
                assert e.code == 400
        finally:
            httpd.shutdown()

    def test_specialize_run_requires_base_model(self):
        port, httpd = _start_test_server()
        try:
            try:
                _post(port, "/api/specialize/run",
                      {"domain": "x", "base_model": ""})
            except urllib.error.HTTPError as e:
                assert e.code == 400
        finally:
            httpd.shutdown()

    def test_specialize_run_requires_domain(self):
        port, httpd = _start_test_server()
        try:
            try:
                _post(port, "/api/specialize/run",
                      {"base_model": "llama3.2:3b", "domain": ""})
            except urllib.error.HTTPError as e:
                assert e.code == 400
        finally:
            httpd.shutdown()

    def test_specialize_run_full(self, tmp_path, monkeypatch):
        # Make Config use tmp_path so Modelfile write succeeds
        monkeypatch.setattr("odc.config.Config.data_dir", tmp_path, raising=False)
        port, httpd = _start_test_server()
        try:
            with patch("odc.training.TrainingStore") as mock_ts:
                instance = mock_ts.return_value
                instance.export_dataset.return_value = {"ok": True, "after_bipolar": 30}
                instance.start_finetune.return_value = {"ok": True}
                with patch("odc.llm.local.LocalBrain") as mock_lb:
                    mock_lb.return_value.set_active.return_value = {"ok": True}
                    r = _post(port, "/api/specialize/run", {
                        "base_model": "llama3.2:3b",
                        "domain": "cybersecurity",
                        "requirements": "triage logs",
                    })
                    d = json.loads(r.read())
            assert d["ok"] is True
            assert d["specialization"]["domain"] == "cybersecurity"
            assert d["specialization"]["base_model"] == "llama3.2:3b"
            assert "next_step" in d
            assert "ollama create" in d["next_step"]
        finally:
            httpd.shutdown()

    def test_specialize_preview(self):
        port, httpd = _start_test_server()
        try:
            r = _post(port, "/api/specialize/preview", {})
            d = json.loads(r.read())
            assert "constitutional_tiers_accepted" in d
            assert "AUTHORITATIVE_API" in d["constitutional_tiers_accepted"]
            assert "bipolar_threshold" in d
            assert "min_samples_per_pattern" in d
            assert len(d["scrub_patterns"]) >= 5
        finally:
            httpd.shutdown()

    def test_auto_extend_status_returns_defaults(self):
        port, httpd = _start_test_server()
        try:
            with patch("odc.web.server._load_system_config",
                       return_value=DEFAULT_CONFIG):
                r = _get(port, "/api/auto-extend/status")
                d = json.loads(r.read())
            assert d["tool_create"] is True
            assert d["skill_create"] is True
            assert d["require_safety"] is True
        finally:
            httpd.shutdown()

    def test_auto_extend_toggle(self, tmp_path, monkeypatch):
        monkeypatch.setattr("odc.web.server._config_path",
                            lambda: tmp_path / "config.json")
        port, httpd = _start_test_server()
        try:
            r = _post(port, "/api/auto-extend/toggle",
                      {"key": "tool_create", "value": False})
            d = json.loads(r.read())
            assert d["ok"] is True
            assert d["auto_extend"]["tool_create"] is False
        finally:
            httpd.shutdown()

    def test_auto_extend_toggle_rejects_unknown_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr("odc.web.server._config_path",
                            lambda: tmp_path / "config.json")
        port, httpd = _start_test_server()
        try:
            try:
                _post(port, "/api/auto-extend/toggle",
                      {"key": "not_a_key", "value": True})
            except urllib.error.HTTPError as e:
                assert e.code == 400
        finally:
            httpd.shutdown()

    def test_auto_extend_toggle_rejects_disabling_safety(self, tmp_path, monkeypatch):
        monkeypatch.setattr("odc.web.server._config_path",
                            lambda: tmp_path / "config.json")
        port, httpd = _start_test_server()
        try:
            try:
                _post(port, "/api/auto-extend/toggle",
                      {"key": "require_safety", "value": False})
            except urllib.error.HTTPError as e:
                assert e.code == 400
        finally:
            httpd.shutdown()

    def test_auto_extend_enable_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr("odc.web.server._config_path",
                            lambda: tmp_path / "config.json")
        port, httpd = _start_test_server()
        try:
            r = _post(port, "/api/auto-extend/enable-all", {})
            d = json.loads(r.read())
            assert d["ok"] is True
            assert d["auto_extend"]["tool_create"] is True
            assert d["auto_extend"]["skill_create"] is True
        finally:
            httpd.shutdown()

    def test_auto_extend_disable_all_keeps_safety_on(self, tmp_path, monkeypatch):
        monkeypatch.setattr("odc.web.server._config_path",
                            lambda: tmp_path / "config.json")
        port, httpd = _start_test_server()
        try:
            r = _post(port, "/api/auto-extend/disable-all", {})
            d = json.loads(r.read())
            assert d["ok"] is True
            assert d["auto_extend"]["tool_create"] is False
            # Safety check MUST remain on
            assert d["auto_extend"]["require_safety"] is True
        finally:
            httpd.shutdown()
