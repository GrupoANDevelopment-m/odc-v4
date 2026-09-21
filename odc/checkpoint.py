"""Loop checkpointing: persist conversation state to SQLite for crash recovery.

A checkpoint is written after each turn of the loop. On restart, the
daemon (or the user) can pass a `thread_id` to resume from the last
checkpoint instead of starting over.

Storage: sqlite (stdlib). One file at <data_dir>/checkpoints.db.
Schema:
  checkpoints(
    thread_id TEXT,
    turn INTEGER,
    task TEXT,
    messages_json TEXT,
    state_json TEXT,
    created_at REAL,
    PRIMARY KEY (thread_id, turn)
  )

We keep the LATEST row per thread for resume, plus an audit trail
of every checkpoint. The audit trail is pruned to the last 20 per
thread by default to avoid unbounded growth.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class LoopCheckpoint:
    """SQLite-backed loop checkpoint store."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    task TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (thread_id, turn)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_checkpoints_thread
                ON checkpoints(thread_id, turn DESC)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_meta (
                    thread_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_turn INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    note TEXT
                )
            """)

    def save(
        self,
        thread_id: str,
        turn: int,
        task: str,
        messages: list[dict[str, Any]],
        state: dict[str, Any] | None = None,
    ) -> None:
        """Persist a checkpoint. Overwrites any existing row at (thread_id, turn)."""
        msgs_json = json.dumps(messages, ensure_ascii=False, default=str)
        state_json = json.dumps(state or {}, ensure_ascii=False, default=str)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints
                    (thread_id, turn, task, messages_json, state_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (thread_id, turn, task, msgs_json, state_json, now),
            )
            conn.execute(
                """
                INSERT INTO task_meta (thread_id, status, last_turn, updated_at, note)
                VALUES (?, 'running', ?, ?, NULL)
                ON CONFLICT(thread_id) DO UPDATE SET
                    status='running', last_turn=excluded.last_turn,
                    updated_at=excluded.updated_at
                """,
                (thread_id, turn, now),
            )
        # Audit trail pruning: keep only the last 20 per thread
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM checkpoints
                WHERE thread_id = ? AND turn NOT IN (
                    SELECT turn FROM checkpoints
                    WHERE thread_id = ?
                    ORDER BY turn DESC
                    LIMIT 20
                )
                """,
                (thread_id, thread_id),
            )

    def load_latest(self, thread_id: str) -> dict[str, Any] | None:
        """Return the most recent checkpoint for thread_id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT turn, task, messages_json, state_json, created_at
                FROM checkpoints
                WHERE thread_id = ?
                ORDER BY turn DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        turn, task, msgs_json, state_json, created_at = row
        return {
            "turn": turn,
            "task": task,
            "messages": json.loads(msgs_json),
            "state": json.loads(state_json),
            "created_at": created_at,
        }

    def load_at(self, thread_id: str, turn: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT turn, task, messages_json, state_json, created_at
                FROM checkpoints
                WHERE thread_id = ? AND turn = ?
                """,
                (thread_id, turn),
            ).fetchone()
        if row is None:
            return None
        turn, task, msgs_json, state_json, created_at = row
        return {
            "turn": turn,
            "task": task,
            "messages": json.loads(msgs_json),
            "state": json.loads(state_json),
            "created_at": created_at,
        }

    def mark_done(self, thread_id: str, status: str = "completed", note: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_meta SET status=?, updated_at=?, note=?
                WHERE thread_id=?
                """,
                (status, time.time(), note, thread_id),
            )

    def list_threads(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT thread_id, status, last_turn, updated_at, note FROM task_meta WHERE status=? ORDER BY updated_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT thread_id, status, last_turn, updated_at, note FROM task_meta ORDER BY updated_at DESC"
                ).fetchall()
        return [
            {
                "thread_id": tid,
                "status": s,
                "last_turn": lt,
                "updated_at": ua,
                "note": n or "",
            }
            for tid, s, lt, ua, n in rows
        ]

    def delete_thread(self, thread_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM checkpoints WHERE thread_id=?", (thread_id,))
            conn.execute("DELETE FROM task_meta WHERE thread_id=?", (thread_id,))

    def prune_older_than(self, days: float) -> int:
        cutoff = time.time() - days * 86400
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM checkpoints WHERE created_at < ?", (cutoff,),
            )
            n1 = cur.rowcount
            cur = conn.execute(
                "DELETE FROM task_meta WHERE updated_at < ?", (cutoff,),
            )
            n2 = cur.rowcount
        return n1 + n2
