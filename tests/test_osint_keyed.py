"""Tests for keyed OSINT tools — graceful degradation when keys missing."""
import asyncio
import os

import pytest

from odc.osint.tools_keyed import (
    sanctions, satellites, fires, eonet, news,
    ALL_KEYED_OSINT_TOOLS, _key_or_error,
)
from odc.tools.base import Tool


def _run(coro):
    return asyncio.run(coro)


def _call(tool_obj, **kwargs):
    return _run(tool_obj.run(**kwargs))


# ──────────────────────────────────────────────────────────────────
# Registry sanity
# ──────────────────────────────────────────────────────────────────
def test_keyed_tools_registered():
    assert len(ALL_KEYED_OSINT_TOOLS) == 5
    names = {t.name for t in ALL_KEYED_OSINT_TOOLS}
    assert "osint.sanctions" in names
    assert "osint.satellites" in names
    assert "osint.fires" in names
    assert "osint.eonet" in names
    assert "osint.news" in names
    for t in ALL_KEYED_OSINT_TOOLS:
        assert isinstance(t, Tool)


# ──────────────────────────────────────────────────────────────────
# Graceful degradation without keys
# ──────────────────────────────────────────────────────────────────
def test_sanctions_without_key_returns_structured_error(monkeypatch):
    monkeypatch.delenv("OPENSANCTIONS_API_KEY", raising=False)
    out = _call(sanctions, query="Putin")
    assert out.get("_available") is False
    assert "OPENSANCTIONS_API_KEY" in out.get("env", "")


def test_satellites_without_key_returns_error(monkeypatch):
    monkeypatch.delenv("N2YO_API_KEY", raising=False)
    out = _call(satellites, norad_id=25544)
    assert out.get("_available") is False
    assert "N2YO_API_KEY" in out.get("env", "")


def test_fires_without_key_returns_error(monkeypatch):
    monkeypatch.delenv("NASA_FIRMS_MAP_KEY", raising=False)
    out = _call(fires, bbox="-75,-15,-45,5", days=1)
    assert out.get("_available") is False
    assert "NASA_FIRMS_MAP_KEY" in out.get("env", "")


# ──────────────────────────────────────────────────────────────────
# EONET and News work without keys (open data)
# ──────────────────────────────────────────────────────────────────
def test_eonet_works_without_key(monkeypatch):
    monkeypatch.delenv("NASA_EONET_API_KEY", raising=False)
    try:
        out = _call(eonet, status="open", limit=5)
    except Exception as e:
        pytest.skip(f"EONET unreachable: {e}")
    assert "events" in out
    assert "source" in out


# News test skipped by default — GDELT is flaky
@pytest.mark.skipif(os.environ.get("SKIP_GDELT"), reason="GDELT skipped")
def test_news_may_work_without_key():
    try:
        out = _call(news, query="earthquake", max_records=3)
    except Exception as e:
        pytest.skip(f"GDELT unreachable: {e}")
    # Either returns articles or _available=False (acceptable)
    assert "articles" in out or out.get("_available") is False


# ──────────────────────────────────────────────────────────────────
# Key helper
# ──────────────────────────────────────────────────────────────────
def test_key_or_error_returns_error_when_missing(monkeypatch):
    monkeypatch.delenv("TEST_KEY_VAR", raising=False)
    r = _key_or_error("TEST_KEY_VAR")
    assert "error" in r
    assert r["_available"] is False


def test_key_or_error_returns_key_when_set(monkeypatch):
    monkeypatch.setenv("TEST_KEY_VAR", "abc123")
    r = _key_or_error("TEST_KEY_VAR")
    assert r["_key"] == "abc123"


# ──────────────────────────────────────────────────────────────────
# Integration: agent registers keyed tools
# ──────────────────────────────────────────────────────────────────
def test_keyed_tools_in_agent_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("ODC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ODC_LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-not-used")

    from odc.config import Config
    from odc.agent import Agent
    cfg = Config(data_dir=tmp_path / "data")
    a = Agent(config=cfg, with_memory=False)

    names = set(a.tools.names())
    expected = {"osint.sanctions", "osint.satellites", "osint.fires",
                "osint.eonet", "osint.news"}
    assert expected.issubset(names), f"missing: {expected - names}"
