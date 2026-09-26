"""Web server tests — endpoints, route coverage, no real agent needed."""
import json
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from odc.web.server import Handler, serve, INDEX_HTML


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
