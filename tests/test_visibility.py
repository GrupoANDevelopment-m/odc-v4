"""Visibility endpoints tests — capabilities, decisions, cognition, upload."""
import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from odc.web.server import Handler
from odc.web.visibility import (
    get_capabilities, get_decisions, get_cognition_state,
    save_upload, list_uploads, parse_multipart, find_boundary,
    MAX_UPLOAD_BYTES,
)
import odc.web.visibility as _vis  # avoid name collision with stdlib `web`


def _start_test_server():
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return port, httpd


def _get(port, path):
    return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15)


def _post(port, path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=15)


def _raw_post(port, path, body, content_type):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=15)


# ── capabilities ────────────────────────────────────────────────
class TestCapabilities:
    def test_capabilities_returns_inventory(self):
        d = get_capabilities()
        assert "tools" in d
        assert "skills" in d
        assert "mcps" in d
        assert "categories" in d
        assert "dynamic_tools" in d

    def test_capabilities_endpoint(self):
        port, httpd = _start_test_server()
        try:
            # Just verify endpoint exists and returns 200
            r = _get(port, "/api/capabilities")
            assert r.status == 200
        finally:
            httpd.shutdown()

    def test_capabilities_includes_categories(self):
        d = get_capabilities()
        cats = d["categories"]
        # Expect at least: code, cognitive, fs, osint, etc.
        assert len(cats) >= 5

    def test_capabilities_dyn_lists_offline(self):
        d = get_capabilities()
        # dynamic_tools/skills are lists (possibly empty)
        assert isinstance(d["dynamic_tools"], list)
        assert isinstance(d["dynamic_skills"], list)


# ── decisions ───────────────────────────────────────────────────
class TestDecisions:
    def test_decisions_returns_list(self):
        d = get_decisions(limit=5)
        assert "decisions" in d
        assert "total" in d

    def test_decisions_no_audit_db(self, tmp_path, monkeypatch):
        # No audit db → empty decisions
        from odc.config import Config
        monkeypatch.setattr("odc.web.visibility._cfg",
                            lambda: Config.__new__(Config))
        d = get_decisions()
        assert d.get("decisions") == []
        assert "note" in d

    def test_decisions_endpoint(self):
        port, httpd = _start_test_server()
        try:
            r = _post(port, "/api/decisions",
                      {"limit": 10, "tool": None, "only_allowed": None})
            data = json.loads(r.read())
            assert "decisions" in data
        finally:
            httpd.shutdown()

    def test_decisions_with_filter(self):
        port, httpd = _start_test_server()
        try:
            r = _post(port, "/api/decisions",
                      {"limit": 5, "tool": "fs.read", "only_allowed": "true"})
            data = json.loads(r.read())
            assert "decisions" in data
        finally:
            httpd.shutdown()


# ── cognition state ─────────────────────────────────────────────
class TestCognition:
    def test_returns_state(self):
        d = get_cognition_state()
        assert "profile" in d
        assert "recent_insights" in d
        assert "heuristics_count" in d
        assert "recent_reflections" in d

    def test_endpoint_exists(self):
        port, httpd = _start_test_server()
        try:
            r = _get(port, "/api/cognition/state")
            data = json.loads(r.read())
            assert "recent_insights" in data
        finally:
            httpd.shutdown()


# ── upload ──────────────────────────────────────────────────────
class TestUpload:
    def test_save_upload_writes_file(self, tmp_path):
        # Save manually — we can't easily call the agent via the endpoint
        # without HTTP overhead, so test save_upload directly with monkeypatched config
        from odc.config import Config
        cfg_orig = Config
        # Patch
        from odc.web import visibility as vis
        vis._cfg = lambda: type("C", (), {"data_dir": tmp_path})()
        result = vis.save_upload(b"hello world", "test.txt", "text/plain")
        assert result["ok"] is True
        assert "filename" in result
        # File exists
        target = Path(result["path"])
        assert target.exists()
        assert target.read_bytes() == b"hello world"

    def test_save_upload_sanitizes_filename(self, tmp_path):
        from odc.web import visibility as vis
        vis._cfg = lambda: type("C", (), {"data_dir": tmp_path})()
        result = vis.save_upload(b"data", "../../etc/passwd", "text/plain")
        assert result["ok"] is True
        # No path traversal — file is inside uploads/
        target = Path(result["path"])
        assert tmp_path in target.parents
        assert "passwd" in target.name

    def test_save_upload_rejects_too_large(self, tmp_path):
        from odc.web import visibility as vis
        vis._cfg = lambda: type("C", (), {"data_dir": tmp_path})()
        # 501 MB
        big = b"x" * (501 * 1024 * 1024)
        result = vis.save_upload(big, "huge.bin", "application/octet-stream")
        assert result["ok"] is False
        assert "too large" in result["error"]

    def test_list_uploads(self, tmp_path):
        from odc.web import visibility as vis
        vis._cfg = lambda: type("C", (), {"data_dir": tmp_path})()
        # No uploads dir yet → empty
        d = vis.list_uploads()
        assert d["uploads"] == []
        # Save one, then list
        vis.save_upload(b"hi", "x.txt", "text/plain")
        d = vis.list_uploads()
        assert len(d["uploads"]) == 1
        assert d["max_size"] == MAX_UPLOAD_BYTES


# ── multipart parsing ──────────────────────────────────────────
class TestMultipart:
    def test_find_boundary_double_quoted(self):
        assert find_boundary('multipart/form-data; boundary="abc123"') == "abc123"

    def test_find_boundary_unquoted(self):
        assert find_boundary("multipart/form-data; boundary=xyz789") == "xyz789"

    def test_find_boundary_missing(self):
        assert find_boundary("text/plain") is None

    def test_parse_simple_field(self):
        body = (
            b"--BOUND\r\n"
            b'Content-Disposition: form-data; name="foo"\r\n\r\n'
            b"bar\r\n"
            b"--BOUND--\r\n"
        )
        result = parse_multipart(
            {"content-type": "multipart/form-data; boundary=BOUND"},
            body, "BOUND"
        )
        assert result["fields"]["foo"] == "bar"
        assert result["files"] == []

    def test_parse_file_upload(self):
        file_data = b"hello file content"
        body = (
            b"--BOUND\r\n"
            b'Content-Disposition: form-data; name="upload"; filename="hi.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n"
            + file_data + b"\r\n"
            b"--BOUND--\r\n"
        )
        result = parse_multipart(
            {"content-type": "multipart/form-data; boundary=BOUND"},
            body, "BOUND"
        )
        assert len(result["files"]) == 1
        f = result["files"][0]
        assert f["filename"] == "hi.txt"
        assert f["name"] == "upload"
        assert f["content_type"] == "text/plain"
        # File data is preserved (with maybe trailing newline stripped)
        assert f["data"].startswith(file_data)


# ── upload endpoint ─────────────────────────────────────────────
class TestUploadEndpoint:
    def test_upload_endpoint_rejects_non_multipart(self):
        port, httpd = _start_test_server()
        try:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/upload",
                    data=b'{"x":1}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as e:
                assert e.code == 400
        finally:
            httpd.shutdown()

    def test_upload_endpoint_accepts_multipart(self):
        port, httpd = _start_test_server()
        try:
            body = (
                b"--BOUND\r\n"
                b'Content-Disposition: form-data; name="upload"; filename="hi.txt"\r\n'
                b"Content-Type: text/plain\r\n\r\n"
                b"hello\r\n"
                b"--BOUND--\r\n"
            )
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/upload",
                data=body,
                headers={"Content-Type": "multipart/form-data; boundary=BOUND"},
                method="POST",
            )
            r = urllib.request.urlopen(req, timeout=5)
            data = json.loads(r.read())
            assert data["ok"] is True
            assert "hi.txt" in data["filename"]
        finally:
            httpd.shutdown()


# ── uploads listing endpoint ───────────────────────────────────
class TestUploadsListEndpoint:
    def test_uploads_list_endpoint(self):
        port, httpd = _start_test_server()
        try:
            r = _get(port, "/api/uploads")
            data = json.loads(r.read())
            assert "uploads" in data
            assert "max_size" in data
        finally:
            httpd.shutdown()
