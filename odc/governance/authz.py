"""Authorization: workspace roots, web allow-list, RBAC.

Post-audit (2026-09-23): the previous design had `fs.read` accepting
absolute paths and reading any file the process could access. `fs.write`
could overwrite any path. This module introduces:

  1. WorkspacePolicy — list of allowed filesystem roots
  2. WebPolicy — domain allow-list for web tools
  3. CapabilityToken — signed (HMAC) capability for a (user, tool, scope) tuple
  4. AuthorizationGuard — checks before each tool call
  5. Immutable audit trail — append-only hash-chained

DESIGN:

  • Each tool declares its required capability via metadata.
  • Before invocation, the AuthorizationGuard verifies the user's
    CapabilityToken against the tool's required scope.
  • fs.read/write/edit check the path is inside an allowed workspace_root.
  • web.fetch checks the domain is in the allow-list.
  • Every authorization decision is logged to an append-only audit log
    with HMAC chaining (each entry hash = prev_hash || entry).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# WorkspacePolicy — list of allowed filesystem roots
# ──────────────────────────────────────────────────────────────────
@dataclass
class WorkspacePolicy:
    """List of allowed filesystem roots for read/write operations.

    A path is allowed if it resolves to a subdirectory of one of the
    allowed roots. Symlinks are resolved before checking.
    """
    allowed_roots: list[Path] = field(default_factory=list)
    deny_patterns: list[str] = field(default_factory=list)
    max_file_size_mb: float = 100.0

    def allows(self, path: str | Path, op: str = "read") -> tuple[bool, str]:
        """Return (allowed, reason). op is 'read' or 'write'."""
        try:
            p = Path(path).resolve(strict=False)
        except (OSError, RuntimeError) as e:
            return False, f"cannot resolve path: {e}"
        # Check it's inside one of the allowed roots
        for root in self.allowed_roots:
            try:
                root_resolved = root.resolve(strict=False)
                p.relative_to(root_resolved)
                # Inside an allowed root; check deny patterns
                for pat in self.deny_patterns:
                    if pat in str(p):
                        return False, f"matches deny pattern: {pat}"
                # Check size if file exists
                if p.is_file() and op == "read":
                    size_mb = p.stat().st_size / (1024 * 1024)
                    if size_mb > self.max_file_size_mb:
                        return False, f"file too large: {size_mb:.1f}MB > {self.max_file_size_mb}MB"
                return True, f"allowed by root {root}"
            except ValueError:
                continue
        return False, f"path {p} not under any allowed root {self.allowed_roots}"


# ──────────────────────────────────────────────────────────────────
# WebPolicy — domain allow-list
# ──────────────────────────────────────────────────────────────────
@dataclass
class WebPolicy:
    """Domain allow-list for web.fetch, web.search, OSINT tools."""
    allowed_domains: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)
    allowed_schemes: tuple[str, ...] = ("http", "https")
    deny_private_ips: bool = True  # 127.0.0.1, 10.x, 192.168.x, etc.

    def allows_url(self, url: str) -> tuple[bool, str]:
        try:
            u = urlparse(url)
        except Exception as e:
            return False, f"invalid URL: {e}"
        if u.scheme not in self.allowed_schemes:
            return False, f"scheme {u.scheme!r} not in {self.allowed_schemes}"
        host = (u.hostname or "").lower()
        if not host:
            return False, "no host in URL"
        if self.deny_private_ips and (
            host.startswith("127.") or host.startswith("10.") or
            host.startswith("192.168.") or host.startswith("169.254.") or
            host == "localhost" or host == "0.0.0.0"
        ):
            return False, "private IP/host denied"
        for d in self.denied_domains:
            if host == d or host.endswith("." + d):
                return False, f"domain {d} denied"
        for d in self.allowed_domains:
            if host == d or host.endswith("." + d):
                return True, f"allowed by {d}"
        return False, f"host {host} not in allow-list"


# ──────────────────────────────────────────────────────────────────
# CapabilityToken — signed (HMAC) capability for a (user, tool, scope) tuple
# ──────────────────────────────────────────────────────────────────
@dataclass
class CapabilityToken:
    """HMAC-signed token granting a specific capability.

    token = base64(json({user, tool, scope, exp})) + "." + hmac_sig
    """
    user: str
    tool: str           # tool name, or "*" for wildcard
    scope: dict[str, Any]  # e.g., {"paths": ["/data/x"], "domains": ["api.example.com"]}
    expires_at: float
    issued_at: float = field(default_factory=time.time)
    secret: bytes = field(default_factory=lambda: os.urandom(32))
    raw_token: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user": self.user,
            "tool": self.tool,
            "scope": self.scope,
            "exp": self.expires_at,
            "iat": self.issued_at,
        }

    def sign(self) -> str:
        """Sign and produce the raw token string."""
        payload = json.dumps(self.to_dict(), sort_keys=True).encode()
        import base64
        b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        sig = hmac.new(self.secret, b64.encode(), hashlib.sha256).hexdigest()
        self.raw_token = f"{b64}.{sig}"
        return self.raw_token

    @classmethod
    def verify(cls, raw: str, secret: bytes) -> "CapabilityToken | None":
        """Verify signature and expiration. Returns None if invalid."""
        import base64
        try:
            b64, sig = raw.split(".", 1)
        except ValueError:
            return None
        expected = hmac.new(secret, b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        # Decode payload
        padding = "=" * (-len(b64) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(b64 + padding))
        except Exception:
            return None
        if payload.get("exp", 0) < time.time():
            return None  # expired
        t = cls(
            user=payload["user"], tool=payload["tool"],
            scope=payload["scope"], expires_at=payload["exp"],
            issued_at=payload["iat"], secret=secret, raw_token=raw,
        )
        return t


# ──────────────────────────────────────────────────────────────────
# AuthorizationGuard — checks before each tool call
# ──────────────────────────────────────────────────────────────────
@dataclass
class AuthDecision:
    allowed: bool
    reason: str
    tool: str
    user: str
    paths: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class AuthorizationGuard:
    """Central guard invoked before side-effecting tools."""

    def __init__(self, workspace: WorkspacePolicy, web: WebPolicy,
                 capability: CapabilityToken | None = None):
        self.workspace = workspace
        self.web = web
        self.capability = capability

    def check_path(self, path: str | Path, op: str = "read") -> AuthDecision:
        allowed, reason = self.workspace.allows(path, op)
        return AuthDecision(
            allowed=allowed, reason=reason, tool="fs",
            user=self.capability.user if self.capability else "anon",
            paths=[str(path)],
        )

    def check_url(self, url: str) -> AuthDecision:
        allowed, reason = self.web.allows_url(url)
        host = urlparse(url).hostname or ""
        return AuthDecision(
            allowed=allowed, reason=reason, tool="web",
            user=self.capability.user if self.capability else "anon",
            domains=[host],
        )

    def check_capability(self, tool_name: str) -> AuthDecision:
        if self.capability is None:
            return AuthDecision(
                allowed=False, reason="no capability token", tool=tool_name,
                user="anon",
            )
        if self.capability.tool != "*" and self.capability.tool != tool_name:
            return AuthDecision(
                allowed=False,
                reason=f"capability is for {self.capability.tool}, not {tool_name}",
                tool=tool_name, user=self.capability.user,
            )
        if self.capability.expires_at < time.time():
            return AuthDecision(
                allowed=False, reason="capability expired",
                tool=tool_name, user=self.capability.user,
            )
        return AuthDecision(
            allowed=True, reason="capability valid",
            tool=tool_name, user=self.capability.user,
        )


# ──────────────────────────────────────────────────────────────────
# Immutable audit trail — append-only hash-chained
# ──────────────────────────────────────────────────────────────────
class AuditTrail:
    """Append-only audit log with HMAC chaining.

    Each entry's hash = hmac(prev_hash || entry_payload), so any
    tampering with prior entries is detectable.

    Storage: SQLite at <data_dir>/governance/audit.db.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS audit (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        prev_hash TEXT NOT NULL,
        entry_hash TEXT NOT NULL,
        user TEXT,
        tool TEXT,
        decision TEXT,           -- 'allow' | 'deny'
        reason TEXT,
        payload TEXT             -- JSON of full AuthDecision
    );
    """

    def __init__(self, db_path: Path | str, secret: bytes | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._secret = secret or os.urandom(32)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    def append(self, decision: AuthDecision) -> int:
        with self._lock:
            prev_row = self._conn.execute(
                "SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = prev_row[0] if prev_row else "0" * 64
            payload = json.dumps({
                "ts": decision.timestamp,
                "user": decision.user,
                "tool": decision.tool,
                "decision": "allow" if decision.allowed else "deny",
                "reason": decision.reason,
                "paths": decision.paths,
                "domains": decision.domains,
            }, sort_keys=True).encode()
            entry_hash = hmac.new(
                self._secret, prev_hash.encode() + payload, hashlib.sha256,
            ).hexdigest()
            cur = self._conn.execute(
                "INSERT INTO audit(ts, prev_hash, entry_hash, user, tool, "
                "decision, reason, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (decision.timestamp, prev_hash, entry_hash,
                 decision.user, decision.tool,
                 "allow" if decision.allowed else "deny",
                 decision.reason, payload.decode()),
            )
            self._conn.commit()
            return cur.lastrowid

    def verify_chain(self) -> tuple[bool, int]:
        """Return (is_valid, broken_at_seq). 0 if all good."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, prev_hash, entry_hash, payload, ts, user, tool, "
                "decision, reason FROM audit ORDER BY seq ASC"
            ).fetchall()
        if not rows:
            return (True, 0)
        expected_prev = "0" * 64
        for row in rows:
            seq, prev_hash, entry_hash, payload, ts, user, tool, dec, reason = row
            if prev_hash != expected_prev:
                return (False, seq)
            expected_entry = hmac.new(
                self._secret, prev_hash.encode() + payload.encode(), hashlib.sha256,
            ).hexdigest()
            if entry_hash != expected_entry:
                return (False, seq)
            expected_prev = entry_hash
        return (True, 0)

    def query(self, user: str = "", tool: str = "",
              decision: str = "", limit: int = 100) -> list[dict]:
        with self._lock:
            sql = "SELECT seq, ts, user, tool, decision, reason, payload " \
                  "FROM audit WHERE 1=1"
            params: list[Any] = []
            if user:
                sql += " AND user=?"
                params.append(user)
            if tool:
                sql += " AND tool=?"
                params.append(tool)
            if decision:
                sql += " AND decision=?"
                params.append(decision)
            sql += " ORDER BY seq DESC LIMIT ?"
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            return [{
                "seq": r[0], "ts": r[1], "user": r[2], "tool": r[3],
                "decision": r[4], "reason": r[5], "payload": r[6],
            } for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
