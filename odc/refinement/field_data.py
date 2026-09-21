"""Field-data empirical record — the agent's own audit trail of outcomes."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    task_type TEXT NOT NULL,           -- domain: 'security', 'osint', 'code', 'web'...
    hypothesis_id TEXT NOT NULL,       -- which heuristic/approach was tried
    lenses_used TEXT,                  -- JSON array: ['hacker','researcher']
    success INTEGER NOT NULL,          -- 0 or 1
    duration_ms INTEGER,
    error_class TEXT,                  -- e.g., 'timeout','rate_limit','not_in_allowlist'
    failure_reason TEXT,
    evidence_tier TEXT,                -- 6-tier from cognitive.evidence
    timestamp REAL,
    metadata TEXT                      -- JSON
);
CREATE INDEX IF NOT EXISTS idx_outcomes_type ON outcomes(task_type);
CREATE INDEX IF NOT EXISTS idx_outcomes_hypo ON outcomes(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_ts ON outcomes(timestamp);
CREATE INDEX IF NOT EXISTS idx_outcomes_succ ON outcomes(success);

CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,               -- 'proposed', 'verified', 'applied', 'rolled_back', 'rejected'
    target TEXT,                       -- which component
    proposal_id TEXT,
    rationale TEXT,
    before_state TEXT,                 -- JSON snapshot
    after_state TEXT,
    success INTEGER,                   -- for apply outcomes
    timestamp REAL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_journal_ts ON journal(timestamp);
"""


@dataclass
class FieldOutcome:
    """One empirical observation of how a hypothesis performed."""
    task_id: str
    task_type: str
    hypothesis_id: str
    success: bool
    duration_ms: int = 0
    error_class: str = ""
    failure_reason: str = ""
    evidence_tier: str = "DIRECT_OBSERVATION"
    lenses_used: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None


@dataclass
class HypothesisStats:
    """Stats for one hypothesis across N outcomes."""
    hypothesis_id: str
    task_type: str
    attempts: int
    successes: int
    failures: int
    failure_rate: float
    avg_duration_ms: float
    most_common_error_class: str
    is_exhausted: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FieldDataStore:
    """Persistent empirical record of all task outcomes.

    Threadsafe SQLite. The agent queries this before making any
    self-refinement decision. Field data is the ground truth.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def record(self, outcome: FieldOutcome) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO outcomes(task_id, task_type, hypothesis_id, lenses_used, "
                "success, duration_ms, error_class, failure_reason, evidence_tier, "
                "timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (outcome.task_id, outcome.task_type, outcome.hypothesis_id,
                 json.dumps(outcome.lenses_used), int(outcome.success),
                 outcome.duration_ms, outcome.error_class, outcome.failure_reason,
                 outcome.evidence_tier, outcome.timestamp,
                 json.dumps(outcome.metadata)),
            )
            self._conn.commit()
            outcome.id = cur.lastrowid
            return cur.lastrowid

    def record_outcomes_batch(self, outcomes: list[FieldOutcome]) -> list[int]:
        """Convenience: record many outcomes in one call."""
        return [self.record(o) for o in outcomes]

    def query(
        self,
        task_type: str | None = None,
        since: float | None = None,
        hypothesis_id: str | None = None,
        success_only: bool = False,
        failure_only: bool = False,
        limit: int = 1000,
    ) -> list[FieldOutcome]:
        with self._lock:
            sql = "SELECT id, task_id, task_type, hypothesis_id, lenses_used, success, " \
                  "duration_ms, error_class, failure_reason, evidence_tier, timestamp, metadata " \
                  "FROM outcomes WHERE 1=1"
            params: list[Any] = []
            if task_type:
                sql += " AND task_type=?"
                params.append(task_type)
            if since is not None:
                sql += " AND timestamp>=?"
                params.append(since)
            if hypothesis_id:
                sql += " AND hypothesis_id=?"
                params.append(hypothesis_id)
            if success_only:
                sql += " AND success=1"
            if failure_only:
                sql += " AND success=0"
            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            return [_row_to_outcome(r) for r in rows]

    def stats_for(
        self,
        task_type: str,
        min_attempts: int = 1,
    ) -> list[HypothesisStats]:
        """Compute failure rate per hypothesis for a task_type."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT hypothesis_id, COUNT(*) AS n, "
                "SUM(success) AS s, "
                "SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS f, "
                "AVG(duration_ms) AS avg_dur, "
                "error_class, COUNT(error_class) AS ec_count "
                "FROM outcomes WHERE task_type=? AND hypothesis_id IS NOT NULL "
                "GROUP BY hypothesis_id "
                "HAVING n>=? "
                "ORDER BY f DESC, n DESC",
                (task_type, min_attempts),
            ).fetchall()
            # error_class aggregation needs separate query
            stats: list[HypothesisStats] = []
            for r in rows:
                hyp_id, n, s, f, avg_dur, _, _ = r
                ec_rows = self._conn.execute(
                    "SELECT error_class, COUNT(*) FROM outcomes "
                    "WHERE task_type=? AND hypothesis_id=? AND success=0 "
                    "AND error_class IS NOT NULL AND error_class != '' "
                    "GROUP BY error_class ORDER BY 2 DESC LIMIT 1",
                    (task_type, hyp_id),
                ).fetchone()
                common_ec = ec_rows[0] if ec_rows else ""
                stats.append(HypothesisStats(
                    hypothesis_id=hyp_id, task_type=task_type,
                    attempts=n, successes=s or 0, failures=f or 0,
                    failure_rate=(f or 0) / n if n else 0.0,
                    avg_duration_ms=avg_dur or 0.0,
                    most_common_error_class=common_ec,
                    is_exhausted=False,  # set by ExhaustionGate
                ))
            return stats

    def record_journal(
        self,
        event: str,
        target: str = "",
        proposal_id: str = "",
        rationale: str = "",
        before_state: dict | None = None,
        after_state: dict | None = None,
        success: bool | None = None,
        metadata: dict | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO journal(event, target, proposal_id, rationale, "
                "before_state, after_state, success, timestamp, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event, target, proposal_id, rationale,
                 json.dumps(before_state or {}), json.dumps(after_state or {}),
                 None if success is None else int(success), time.time(),
                 json.dumps(metadata or {})),
            )
            self._conn.commit()
            return cur.lastrowid

    def journal(
        self,
        proposal_id: str | None = None,
        event: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            sql = "SELECT id, event, target, proposal_id, rationale, before_state, " \
                  "after_state, success, timestamp FROM journal WHERE 1=1"
            params: list[Any] = []
            if proposal_id:
                sql += " AND proposal_id=?"
                params.append(proposal_id)
            if event:
                sql += " AND event=?"
                params.append(event)
            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            return [{
                "id": r[0], "event": r[1], "target": r[2], "proposal_id": r[3],
                "rationale": r[4], "before_state": json.loads(r[5] or "{}"),
                "after_state": json.loads(r[6] or "{}"),
                "success": r[7], "timestamp": r[8],
            } for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


def _row_to_outcome(row: tuple) -> FieldOutcome:
    return FieldOutcome(
        id=row[0],
        task_id=row[1],
        task_type=row[2],
        hypothesis_id=row[3],
        lenses_used=json.loads(row[4] or "[]"),
        success=bool(row[5]),
        duration_ms=row[6] or 0,
        error_class=row[7] or "",
        failure_reason=row[8] or "",
        evidence_tier=row[9] or "DIRECT_OBSERVATION",
        timestamp=row[10],
        metadata=json.loads(row[11] or "{}"),
    )
