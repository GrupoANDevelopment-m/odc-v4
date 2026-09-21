"""Tests for the memory store: save, search, recent, persistence."""
from __future__ import annotations

import time
from pathlib import Path

from odc.memory.store import MemoryStore


def test_save_and_count(tmp_path: Path):
    s = MemoryStore(tmp_path / "m.db")
    assert s.count() == 0
    s.save("the deploy command is `odc deploy`", category="fact", tags=["deploy"])
    s.save("user prefers dark mode", category="user")
    assert s.count() == 2


def test_search_finds_relevant(tmp_path: Path):
    s = MemoryStore(tmp_path / "m.db")
    s.save("Python 3.12 added type aliases", category="fact", tags=["python"])
    s.save("JavaScript has BigInt since ES2020", category="fact", tags=["js"])
    s.save("user prefers vim over emacs", category="user")
    hits = s.search("python type aliases")
    assert len(hits) >= 1
    assert any("Python" in h["text"] for h in hits)


def test_search_empty_query(tmp_path: Path):
    s = MemoryStore(tmp_path / "m.db")
    s.save("anything")
    assert s.search("") == []
    assert s.search("   ") == []


def test_search_category_filter(tmp_path: Path):
    s = MemoryStore(tmp_path / "m.db")
    s.save("apple is a fruit", category="fact")
    s.save("user likes apples", category="user")
    a = s.search("apple", category="fact")
    assert all(h["category"] == "fact" for h in a)


def test_recent_ordering(tmp_path: Path):
    s = MemoryStore(tmp_path / "m.db")
    for i in range(5):
        s.save(f"entry {i}")
        time.sleep(0.01)
    recent = s.recent(limit=3)
    assert len(recent) == 3
    # Newest first
    assert "entry 4" in recent[0]["text"]


def test_delete(tmp_path: Path):
    s = MemoryStore(tmp_path / "m.db")
    eid = s.save("temporary")
    assert s.count() == 1
    assert s.delete(eid) is True
    assert s.count() == 0
    assert s.delete(eid) is False


def test_get(tmp_path: Path):
    s = MemoryStore(tmp_path / "m.db")
    eid = s.save("hello", category="note", tags=["a", "b"])
    entry = s.get(eid)
    assert entry is not None
    assert entry["text"] == "hello"
    assert entry["tags"] == ["a", "b"]


def test_persistence_across_instances(tmp_path: Path):
    db = tmp_path / "persist.db"
    s1 = MemoryStore(db)
    s1.save("survives restart", category="fact")
    s1.close()

    s2 = MemoryStore(db)
    hits = s2.search("survives")
    assert any("survives" in h["text"] for h in hits)
