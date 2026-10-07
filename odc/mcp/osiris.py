"""MCP memory substrate (5-step ritual, SQLite backend)."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Schema (forward-compatible with asuramaya/Osiris Evidence Taxonomy)
# ──────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    cwd TEXT,
    lineage TEXT,
    mounted_at REAL,
    settled_at REAL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    content_hash TEXT UNIQUE,
    category TEXT,            -- 'architectural', 'tool_choice', 'fact', 'anti_pattern'
    decision TEXT,            -- the actual choice
    rationale TEXT,
    evidence_tier TEXT,       -- 6-tier: SELF_DECLARED, AUTHORITATIVE_API, etc.
    parent_id INTEGER REFERENCES decisions(id),
    created_at REAL,
    seen_in_threads INTEGER DEFAULT 1,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_decisions_session ON decisions(session_id);
CREATE INDEX IF NOT EXISTS idx_decisions_category ON decisions(category);
CREATE INDEX IF NOT EXISTS idx_decisions_hash ON decisions(content_hash);

CREATE TABLE IF NOT EXISTS rulings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    key TEXT UNIQUE,
    value TEXT,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS postal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT,
    recipient TEXT,           -- agent id, channel, or 'broadcast'
    kind TEXT,                -- 'note', 'decision_ref', 'request'
    body TEXT,
    thread TEXT,
    parent_id INTEGER REFERENCES postal(id),
    created_at REAL,
    read_at REAL
);

CREATE INDEX IF NOT EXISTS idx_postal_recipient ON postal(recipient);
"""


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ──────────────────────────────────────────────────────────────────
# Data shapes
# ──────────────────────────────────────────────────────────────────
@dataclass
class Decision:
    """An architectural or behavioral choice the agent committed to."""
    session_id: str
    decision: str
    category: str = "architectural"
    rationale: str = ""
    evidence_tier: str = "DIRECT_OBSERVATION"
    parent_id: int | None = None
    success_count: int = 0
    failure_count: int = 0
    id: int | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["confidence"] = self.confidence()
        return d

    def confidence(self, alpha: float = 1.0) -> float:
        from odc.cognitive.evidence import TIER_PRIOR, EvidenceTier
        try:
            prior = TIER_PRIOR[EvidenceTier(self.evidence_tier)]
        except (ValueError, KeyError):
            prior = 0.5
        s, f = self.success_count, self.failure_count
        rate = (s + alpha) / (s + f + 2 * alpha)
        return round(prior * rate, 4)


@dataclass
class PostalMessage:
    sender: str
    recipient: str
    kind: str
    body: str
    thread: str = ""
    parent_id: int | None = None
    id: int | None = None
    created_at: float = field(default_factory=time.time)
    read_at: float | None = None


# ──────────────────────────────────────────────────────────────────
# The substrate
# ──────────────────────────────────────────────────────────────────
class OsirisMemory:
    """5-step ritual + persistent storage.

    Threadsafe SQLite under the hood. Multiple agents can share one
    database by passing the same path (forward-compat with multi-agent).
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        # Enable WAL mode for concurrent reader/writer scaling
        # (without this, concurrent threads hit "database is locked")
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.OperationalError:
            pass
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._session_id: str | None = None
        self._cwd: str | None = None
        self._lineage: str = ""

    # ──────────────────────────────────────────────
    # 1. mount(cwd) — bind session, restore lineage
    # ──────────────────────────────────────────────
    def mount(self, cwd: str = "", session_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            sid = session_id or f"s-{int(time.time() * 1000):x}-{_hash(cwd or 'root')[:6]}"
            self._session_id = sid
            self._cwd = cwd
            # Try to restore lineage from last session with same cwd
            cur = self._conn.execute(
                "SELECT session_id, lineage, settled_at FROM sessions "
                "WHERE cwd=? ORDER BY mounted_at DESC LIMIT 1",
                (cwd,),
            )
            row = cur.fetchone()
            if row:
                self._lineage = row[1] or ""
                # Increment lineage version
                self._lineage = f"{self._lineage}→{sid[:8]}" if self._lineage else sid[:8]
            else:
                self._lineage = sid[:8]
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions(session_id, cwd, lineage, mounted_at) "
                "VALUES (?, ?, ?, ?)",
                (sid, cwd, self._lineage, time.time()),
            )
            self._conn.commit()
            return {
                "session_id": sid,
                "lineage": self._lineage,
                "restored_from": row[0] if row else None,
                "cwd": cwd,
            }

    # ──────────────────────────────────────────────
    # 2. get_status() — identity + unread + fleet pulse (<400 chars)
    # ──────────────────────────────────────────────
    def get_status(self) -> str:
        with self._lock:
            unread = self._conn.execute(
                "SELECT COUNT(*) FROM postal WHERE recipient=? AND read_at IS NULL",
                (self._session_id,),
            ).fetchone()[0]
            decisions = self._conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE session_id=?",
                (self._session_id,),
            ).fetchone()[0]
            total_dec = self._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            return (
                f"sid={self._session_id} "
                f"lineage={self._lineage[:24]} "
                f"cwd={(self._cwd or '')[:40]} "
                f"unread={unread} "
                f"decisions_session={decisions} "
                f"decisions_total={total_dec}"
            )

    # ──────────────────────────────────────────────
    # 3. graph_search(query) — check existing rulings
    # ──────────────────────────────────────────────
    def graph_search(self, query: str, *, category: str | None = None,
                     limit: int = 5) -> list[dict[str, Any]]:
        """Find decisions matching query (substring match on decision/rationale)."""
        with self._lock:
            q = f"%{query.lower()}%"
            sql = (
                "SELECT id, session_id, category, decision, rationale, evidence_tier, "
                "success_count, failure_count, created_at "
                "FROM decisions "
                "WHERE (LOWER(decision) LIKE ? OR LOWER(rationale) LIKE ?)"
            )
            params: list[Any] = [q, q]
            if category:
                sql += " AND category=?"
                params.append(category)
            sql += " ORDER BY success_count DESC, created_at DESC LIMIT ?"
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            return [{
                "id": r[0], "session_id": r[1], "category": r[2],
                "decision": r[3], "rationale": r[4], "evidence_tier": r[5],
                "success_count": r[6], "failure_count": r[7], "created_at": r[8],
                "confidence": Decision(
                    session_id=r[1], decision=r[3], evidence_tier=r[5],
                    success_count=r[6], failure_count=r[7],
                ).confidence(),
            } for r in rows]

    # ──────────────────────────────────────────────
    # 4. record_decision — persist with provenance
    # ──────────────────────────────────────────────
    def record_decision(self, decision: Decision) -> int:
        with self._lock:
            assert self._session_id, "must mount() before record_decision()"
            decision.session_id = self._session_id
            content_hash = _hash(decision.decision + decision.rationale + str(decision.parent_id))
            try:
                cur = self._conn.execute(
                    "INSERT INTO decisions(session_id, content_hash, category, decision, "
                    "rationale, evidence_tier, parent_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (decision.session_id, content_hash, decision.category,
                     decision.decision, decision.rationale, decision.evidence_tier,
                     decision.parent_id, decision.created_at),
                )
                self._conn.commit()
                decision.id = cur.lastrowid
                return cur.lastrowid
            except sqlite3.IntegrityError:
                # Duplicate (same content_hash) — return existing id, increment seen
                cur = self._conn.execute(
                    "SELECT id, seen_in_threads FROM decisions WHERE content_hash=?",
                    (content_hash,),
                )
                row = cur.fetchone()
                if row:
                    self._conn.execute(
                        "UPDATE decisions SET seen_in_threads=seen_in_threads+1 "
                        "WHERE id=?",
                        (row[0],),
                    )
                    self._conn.commit()
                    decision.id = row[0]
                    return row[0]
                raise

    def record_outcome(self, decision_id: int, success: bool) -> None:
        """Record empirical outcome for a decision (success or failure)."""
        col = "success_count" if success else "failure_count"
        with self._lock:
            self._conn.execute(
                f"UPDATE decisions SET {col}={col}+1 WHERE id=?",
                (decision_id,),
            )
            self._conn.commit()

    # ──────────────────────────────────────────────
    # Postal channels (forward-compat with multi-agent)
    # ──────────────────────────────────────────────
    def post(self, msg: PostalMessage) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO postal(sender, recipient, kind, body, thread, parent_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (msg.sender, msg.recipient, msg.kind, msg.body,
                 msg.thread, msg.parent_id, msg.created_at),
            )
            self._conn.commit()
            msg.id = cur.lastrowid
            return cur.lastrowid

    def inbox(self, recipient: str, *, mark_read: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, sender, kind, body, thread, created_at "
                "FROM postal WHERE recipient=? AND read_at IS NULL "
                "ORDER BY created_at ASC LIMIT 50",
                (recipient,),
            ).fetchall()
            ids = [r[0] for r in rows]
            if mark_read and ids:
                self._conn.execute(
                    f"UPDATE postal SET read_at=? WHERE id IN ({','.join('?' * len(ids))})",
                    [time.time(), *ids],
                )
                self._conn.commit()
            return [{
                "id": r[0], "sender": r[1], "kind": r[2], "body": r[3],
                "thread": r[4], "created_at": r[5],
            } for r in rows]

    # ──────────────────────────────────────────────
    # 5. settle() — verify durable, mark session ended
    # ──────────────────────────────────────────────
    def settle(self) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET settled_at=? WHERE session_id=?",
                (time.time(), self._session_id),
            )
            self._conn.commit()
            # Vacuum is too expensive — just checkpoint
            self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            self._conn.commit()
            return {"session_id": self._session_id, "settled": True, "ts": time.time()}

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
