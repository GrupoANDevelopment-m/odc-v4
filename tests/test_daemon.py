"""Tests for the daemon: HTTP webhooks, file watcher, identity, proactive."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from odc.daemon.core import Daemon, Trigger
from odc.identity import Identity, load_identity


# ---------------------------------------------------------------- identity


def test_identity_default(tmp_path: Path):
    p = tmp_path / "identity.json"
    ident = Identity(p)
    assert ident.data["name"] == "odc"
    assert "born" in ident.data
    assert p.exists() is False  # not saved until save() is called


def test_identity_save_and_load(tmp_path: Path):
    p = tmp_path / "identity.json"
    ident = Identity(p)
    ident.save()
    assert p.exists()
    ident2 = Identity(p)
    assert ident2.data["name"] == "odc"


def test_identity_rename_records_history(tmp_path: Path):
    p = tmp_path / "identity.json"
    ident = Identity(p)
    ident.rename("cortana", reason="user asked")
    assert ident.data["name"] == "cortana"
    hist = ident.data["history"]
    assert len(hist) == 1
    assert hist[0]["from"] == "odc"
    assert hist[0]["to"] == "cortana"
    assert hist[0]["reason"] == "user asked"


def test_identity_rename_same_name_noop(tmp_path: Path):
    p = tmp_path / "identity.json"
    ident = Identity(p)
    ident.rename("odc", reason="no change")
    assert ident.data["history"] == []


def test_identity_system_block_includes_name(tmp_path: Path):
    p = tmp_path / "identity.json"
    ident = Identity(p)
    ident.rename("cortana")
    block = ident.to_system_block()
    assert "cortana" in block
    assert "Your identity" in block


# ---------------------------------------------------------------- triggers


def test_trigger_has_id_and_fields():
    t = Trigger(kind="webhook", task="do X", source="ci")
    d = t.to_dict()
    assert d["kind"] == "webhook"
    assert d["task"] == "do X"
    assert d["source"] == "ci"
    assert len(d["id"]) == 12


def test_trigger_unique_ids():
    a = Trigger(kind="webhook", task="X")
    b = Trigger(kind="webhook", task="X")
    assert a.id != b.id


# ---------------------------------------------------------------- daemon lifecycle


@pytest.mark.asyncio
async def test_daemon_starts_and_stops(tmp_path: Path):
    """The daemon must start, bind HTTP, and shut down on request."""
    d = Daemon(
        data_dir=tmp_path / "odc",
        host="127.0.0.1",
        port=18766,  # non-default to avoid conflicts
        watch_dirs=[],
        proactive_interval=0,  # disable proactive for this test
    )
    # Run the daemon in the background
    task = asyncio.create_task(d.run())
    # Give it a moment to start
    await asyncio.sleep(0.5)
    assert d.httpd is not None
    # Request stop
    d._request_stop()
    # Wait for the run to finish
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except asyncio.TimeoutError:
        task.cancel()
        pytest.fail("daemon did not stop within 5s")
    # Identity must have been written at least once
    identity_path = tmp_path / "odc" / "identity.json"
    assert identity_path.exists()


# ---------------------------------------------------------------- HTTP endpoints


@pytest.mark.asyncio
async def test_daemon_http_health(tmp_path: Path):
    """GET /health must return ok=True with the agent name."""
    import urllib.request
    d = Daemon(
        data_dir=tmp_path / "odc",
        host="127.0.0.1",
        port=18767,
        proactive_interval=0,
    )
    task = asyncio.create_task(d.run())
    await asyncio.sleep(0.5)
    try:
        with urllib.request.urlopen("http://127.0.0.1:18767/health", timeout=2) as r:
            data = json.loads(r.read())
            assert data["ok"] is True
            assert "name" in data
    finally:
        d._request_stop()
        await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.asyncio
async def test_daemon_http_trigger_queues_task(tmp_path: Path):
    """POST /trigger with a task must enqueue it (the worker is mocked)."""
    import urllib.request
    d = Daemon(
        data_dir=tmp_path / "odc",
        host="127.0.0.1",
        port=18768,
        proactive_interval=0,
    )
    # Replace _process_trigger so we don't actually call the LLM
    d._process_trigger = AsyncMock()  # type: ignore[assignment]
    task = asyncio.create_task(d.run())
    await asyncio.sleep(0.5)
    try:
        body = json.dumps({"task": "compute sha256 of .env"}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:18768/trigger",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read())
            assert data["queued"] is True
            assert "id" in data
        # Wait for the worker to dequeue
        await asyncio.sleep(0.5)
        assert d._process_trigger.await_count >= 1
    finally:
        d._request_stop()
        await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.asyncio
async def test_daemon_http_rename(tmp_path: Path):
    """POST /rename must update the identity and persist it."""
    import urllib.request
    d = Daemon(
        data_dir=tmp_path / "odc",
        host="127.0.0.1",
        port=18769,
        proactive_interval=0,
    )
    task = asyncio.create_task(d.run())
    await asyncio.sleep(0.5)
    try:
        body = json.dumps({"name": "cortana", "reason": "user asked"}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:18769/rename",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read())
            assert data["renamed"] is True
            assert data["name"] == "cortana"
        # Identity must reflect the new name
        assert d.identity.data["name"] == "cortana"
    finally:
        d._request_stop()
        await asyncio.wait_for(task, timeout=5.0)


# ---------------------------------------------------------------- proactive


@pytest.mark.asyncio
async def test_proactive_triggers_on_repeated_failures(tmp_path: Path):
    """If recent tasks failed >=3 times, proactive enqueues a self_assess task."""
    d = Daemon(
        data_dir=tmp_path / "odc",
        host="127.0.0.1",
        port=18770,
        proactive_interval=0,
    )
    # Inject 3 recent failures
    for _ in range(3):
        d.processed.append({
            "id": "x", "kind": "webhook", "task": "x",
            "ok": False, "ts": time.time(),
        })
    # Create the profile so the proactive check has a path to look at
    prof_dir = tmp_path / "odc" / "cognitive"
    prof_dir.mkdir(parents=True, exist_ok=True)
    (prof_dir / "profile.json").write_text(json.dumps({"meta_metrics": {}}))
    d._maybe_act_proactively()
    # Queue should have one new proactive trigger
    assert d.queue.qsize() >= 1
    trigger = d.queue.get_nowait()
    assert trigger.kind == "proactive"
    assert "self_assess" in trigger.task


# ---------------------------------------------------------------- file watcher


@pytest.mark.asyncio
async def test_watcher_detects_new_file(tmp_path: Path):
    """A file appearing in a watched dir must enqueue a trigger."""
    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()
    d = Daemon(
        data_dir=tmp_path / "odc",
        host="127.0.0.1",
        port=18771,
        watch_dirs=[str(watch_dir)],
        proactive_interval=0,
    )
    # First scan on an empty dir: no triggers
    d._scan_watch_dirs()
    assert d.queue.qsize() == 0
    # Create a new file. Sleep to ensure mtime is strictly greater than
    # the scan resolution (some FS have 1s mtime granularity).
    (watch_dir / "new.txt").write_text("new")
    time.sleep(1.1)
    d._scan_watch_dirs()
    assert d.queue.qsize() == 1
    t = d.queue.get_nowait()
    assert t.kind == "file"
    assert "new.txt" in t.task
    # Second scan: nothing new
    d._scan_watch_dirs()
    assert d.queue.qsize() == 0
    # Modify the file: should enqueue
    (watch_dir / "new.txt").write_text("modified content")
    time.sleep(1.1)
    d._scan_watch_dirs()
    assert d.queue.qsize() == 1
    t = d.queue.get_nowait()
    assert t.kind == "file"
    assert "modified" in t.task


# ---------------------------------------------------------------- integration: agent uses identity


def test_agent_includes_identity_in_skills(tmp_path: Path, monkeypatch):
    """Agent must load identity and expose it as a skill."""
    from odc.config import load_config
    from odc.agent import Agent
    cfg = load_config()
    cfg.data_dir = tmp_path / "odc"
    cfg.max_loop_turns = 2
    # Pre-create a named identity
    ident = Identity(cfg.data_dir / "identity.json")
    ident.rename("cortana")
    ident.save()
    agent = Agent(config=cfg, auto_approve=True, interactive=False)
    skill_names = [s.name for s in agent.skills]
    assert "identity" in skill_names
    identity_skill = next(s for s in agent.skills if s.name == "identity")
    assert "cortana" in identity_skill.body
