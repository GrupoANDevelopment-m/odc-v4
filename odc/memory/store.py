"""Persistent memory store.

SQLite + FTS5. Two tables: `entries` (canonical record) and
`entries_fts` (virtual table for full-text search, kept in sync via
triggers). Concurrency via per-connection thread-local + WAL.

Why not embeddings? Three reasons:
1. Zero install footprint — just stdlib sqlite3.
2. Deterministic & debuggable — you can SELECT and look.
3. Good enough for 10k-100k entries, which is plenty for a personal agent.

If the user wants vector search later, we add a vector column + a
faiss/chroma sidecar; the API stays the same.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from odc.observability import get_logger

log = get_logger("odc.memory")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'note',
    tags TEXT NOT NULL DEFAULT '[]',  -- JSON array
    source TEXT NOT NULL DEFAULT 'agent',
    created_at REAL NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'   -- JSON
);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    text,
    category,
    tags,
    content='entries',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, text, category, tags)
    VALUES (new.rowid, new.text, new.category, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, text, category, tags)
    VALUES ('delete', old.rowid, old.text, old.category, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, text, category, tags)
    VALUES ('delete', old.rowid, old.text, old.category, old.tags);
    INSERT INTO entries_fts(rowid, text, category, tags)
    VALUES (new.rowid, new.text, new.category, new.tags);
END;

CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_entries_category ON entries(category);
"""


class MemoryStore:
    """Thread-safe SQLite+FTS5 store for agent memory.

    >>> s = MemoryStore(Path("/tmp/odc_mem.db"))
    >>> s.save("the deploy command is `odc deploy`", category="fact", tags=["deploy"])
    '...'
    >>> s.search("deploy")
    [{'text': 'the deploy command is `odc deploy`', ...}]
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._init_done = False
        # Open one connection to set up the schema.
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()
        self._init_done = True

    # --- internals ---

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            isolation_level=None,  # autocommit; we manage txns explicitly
            check_same_thread=False,
            timeout=10.0,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _conn(self) -> sqlite3.Connection:
        # Each thread gets its own connection.
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._connect()
            self._local.conn = c
        return c

    # --- public API ---

    def save(
        self,
        text: str,
        *,
        category: str = "note",
        tags: list[str] | None = None,
        source: str = "agent",
        meta: dict[str, Any] | None = None,
    ) -> str:
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        entry_id = str(uuid.uuid4())
        now = time.time()
        with self._init_lock:
            self._conn().execute(
                "INSERT INTO entries (id, text, category, tags, source, created_at, meta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id,
                    text.strip(),
                    category,
                    json.dumps(tags or []),
                    source,
                    now,
                    json.dumps(meta or {}),
                ),
            )
        return entry_id

    def search(
        self, query: str, *, limit: int = 5, category: str | None = None
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        # Build an FTS5 MATCH expression from the user's words.
        # Tokenize: split on whitespace, strip non-word chars, drop empties.
        # Then OR the tokens (any-of match). Quote each token to keep
        # punctuation out of the FTS5 grammar.
        import re as _re

        tokens = [
            tok for tok in (_re.sub(r"[^\w]+", "", w) for w in query.split()) if tok
        ]
        if not tokens:
            return []
        # Use prefix-match on each token so stemming + partial words work.
        fts_query = " OR ".join(f'"{tok}"*' for tok in tokens)
        sql = (
            "SELECT e.id, e.text, e.category, e.tags, e.source, e.created_at, e.meta, "
            "bm25(entries_fts) AS rank "
            "FROM entries_fts JOIN entries e ON e.rowid = entries_fts.rowid "
            "WHERE entries_fts MATCH ? "
        )
        params: list[Any] = [fts_query]
        if category:
            sql += "AND e.category = ? "
            params.append(category)
        sql += "ORDER BY rank LIMIT ?"
        params.append(max(1, min(100, limit)))

        rows = self._conn().execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                tags = json.loads(r["tags"])
            except (TypeError, json.JSONDecodeError):
                tags = []
            try:
                meta = json.loads(r["meta"])
            except (TypeError, json.JSONDecodeError):
                meta = {}
            out.append(
                {
                    "id": r["id"],
                    "text": r["text"],
                    "category": r["category"],
                    "tags": tags,
                    "source": r["source"],
                    "created_at": r["created_at"],
                    "meta": meta,
                    "rank": r["rank"],
                }
            )
        return out

    def recent(self, *, limit: int = 10, category: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM entries"
        params: list[Any] = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(100, limit)))
        rows = self._conn().execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                tags = json.loads(r["tags"])
            except (TypeError, json.JSONDecodeError):
                tags = []
            out.append(
                {
                    "id": r["id"],
                    "text": r["text"],
                    "category": r["category"],
                    "tags": tags,
                    "source": r["source"],
                    "created_at": r["created_at"],
                }
            )
        return out

    def get(self, entry_id: str) -> dict[str, Any] | None:
        r = self._conn().execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        # JSON-decode tags and meta so callers don't have to.
        for k in ("tags", "meta"):
            v = d.get(k)
            if isinstance(v, str):
                try:
                    d[k] = json.loads(v)
                except (TypeError, json.JSONDecodeError):
                    d[k] = [] if k == "tags" else {}
        return d

    def delete(self, entry_id: str) -> bool:
        cur = self._conn().execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        return cur.rowcount > 0

    def count(self) -> int:
        r = self._conn().execute("SELECT COUNT(*) AS c FROM entries").fetchone()
        return int(r["c"])

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None
