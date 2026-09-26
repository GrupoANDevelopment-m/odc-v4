"""Tests for authorization: workspace roots, web allow-list, RBAC, audit chain."""
import time
from pathlib import Path

import pytest

from odc.governance.authz import (
    WorkspacePolicy, WebPolicy, CapabilityToken,
    AuthorizationGuard, AuditTrail, AuthDecision,
)


# ──────────────────────────────────────────────────────────────────
# WorkspacePolicy
# ──────────────────────────────────────────────────────────────────
def test_workspace_allows_path_inside_root(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    f = root / "ok.txt"
    f.write_text("hi")
    policy = WorkspacePolicy(allowed_roots=[root])
    allowed, reason = policy.allows(f)
    assert allowed, reason


def test_workspace_denies_path_outside_root(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    policy = WorkspacePolicy(allowed_roots=[root])
    allowed, reason = policy.allows(outside)
    assert not allowed
    assert "not under any allowed root" in reason


def test_workspace_blocks_etc_passwd(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    policy = WorkspacePolicy(allowed_roots=[root])
    allowed, _ = policy.allows("/etc/passwd")
    assert not allowed


def test_workspace_deny_patterns(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    secret_dir = root / "secrets"
    secret_dir.mkdir()
    f = secret_dir / "key.pem"
    f.write_text("...")
    policy = WorkspacePolicy(allowed_roots=[root], deny_patterns=[".pem"])
    allowed, reason = policy.allows(f)
    assert not allowed
    assert ".pem" in reason


def test_workspace_max_file_size(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    f = root / "huge.bin"
    f.write_bytes(b"x" * (200 * 1024 * 1024))  # 200MB
    policy = WorkspacePolicy(allowed_roots=[root], max_file_size_mb=100.0)
    allowed, reason = policy.allows(f)
    assert not allowed
    assert "too large" in reason


# ──────────────────────────────────────────────────────────────────
# WebPolicy
# ──────────────────────────────────────────────────────────────────
def test_web_allows_allowlisted_domain():
    p = WebPolicy(allowed_domains=["api.example.com"])
    allowed, _ = p.allows_url("https://api.example.com/data")
    assert allowed


def test_web_allows_subdomain_of_allowlisted():
    p = WebPolicy(allowed_domains=["example.com"])
    allowed, _ = p.allows_url("https://api.example.com/data")
    assert allowed


def test_web_denies_unknown_domain():
    p = WebPolicy(allowed_domains=["example.com"])
    allowed, _ = p.allows_url("https://malicious.com/data")
    assert not allowed


def test_web_denies_private_ips():
    p = WebPolicy(allowed_domains=["localhost"], deny_private_ips=True)
    allowed, _ = p.allows_url("http://localhost/secrets")
    assert not allowed
    allowed, _ = p.allows_url("http://10.0.0.5/secrets")
    assert not allowed


def test_web_denies_dangerous_schemes():
    p = WebPolicy(allowed_domains=["example.com"])
    assert not p.allows_url("file:///etc/passwd")[0]
    assert not p.allows_url("javascript:alert(1)")[0]


def test_web_denies_explicit_deny():
    p = WebPolicy(allowed_domains=["example.com"], denied_domains=["bad.example.com"])
    assert not p.allows_url("https://bad.example.com/x")[0]


# ──────────────────────────────────────────────────────────────────
# CapabilityToken (HMAC-signed)
# ──────────────────────────────────────────────────────────────────
def test_capability_token_sign_and_verify():
    secret = b"test-secret"
    t = CapabilityToken(
        user="alice", tool="osint.bitcoin", scope={},
        expires_at=time.time() + 3600, secret=secret,
    )
    raw = t.sign()
    verified = CapabilityToken.verify(raw, secret)
    assert verified is not None
    assert verified.user == "alice"
    assert verified.tool == "osint.bitcoin"


def test_capability_token_wrong_secret_fails():
    t = CapabilityToken(user="alice", tool="x", scope={},
                        expires_at=time.time() + 3600)
    raw = t.sign()
    assert CapabilityToken.verify(raw, b"wrong-secret") is None


def test_capability_token_expired():
    t = CapabilityToken(user="alice", tool="x", scope={},
                        expires_at=time.time() - 100)
    raw = t.sign()
    assert CapabilityToken.verify(raw, t.secret) is None


def test_capability_token_tampered_payload_fails():
    t = CapabilityToken(user="alice", tool="x", scope={},
                        expires_at=time.time() + 3600)
    raw = t.sign()
    # Tamper: replace last char of payload part with random
    parts = raw.split(".")
    tampered = parts[0][:-1] + "X" + "." + parts[1]
    assert CapabilityToken.verify(tampered, t.secret) is None


# ──────────────────────────────────────────────────────────────────
# AuthorizationGuard
# ──────────────────────────────────────────────────────────────────
def test_guard_check_path_allowed(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    f = root / "ok.txt"
    f.write_text("hi")
    guard = AuthorizationGuard(
        workspace=WorkspacePolicy(allowed_roots=[root]),
        web=WebPolicy(),
    )
    d = guard.check_path(f)
    assert d.allowed


def test_guard_check_path_blocked(tmp_path):
    guard = AuthorizationGuard(
        workspace=WorkspacePolicy(allowed_roots=[tmp_path / "nope"]),
        web=WebPolicy(),
    )
    d = guard.check_path("/etc/passwd")
    assert not d.allowed


def test_guard_check_url_allowed():
    guard = AuthorizationGuard(
        workspace=WorkspacePolicy(),
        web=WebPolicy(allowed_domains=["opensky-network.org"]),
    )
    d = guard.check_url("https://opensky-network.org/api/states/all")
    assert d.allowed


def test_guard_check_capability_no_token_denies():
    guard = AuthorizationGuard(
        workspace=WorkspacePolicy(), web=WebPolicy(),
    )
    d = guard.check_capability("osint.bitcoin")
    assert not d.allowed
    assert "no capability token" in d.reason


def test_guard_check_capability_wildcard_allows_anything():
    t = CapabilityToken(user="alice", tool="*", scope={},
                        expires_at=time.time() + 3600)
    guard = AuthorizationGuard(
        workspace=WorkspacePolicy(), web=WebPolicy(), capability=t,
    )
    assert guard.check_capability("osint.bitcoin").allowed
    assert guard.check_capability("fs.read").allowed


def test_guard_check_capability_specific_tool_only():
    t = CapabilityToken(user="alice", tool="osint.bitcoin", scope={},
                        expires_at=time.time() + 3600)
    guard = AuthorizationGuard(
        workspace=WorkspacePolicy(), web=WebPolicy(), capability=t,
    )
    assert guard.check_capability("osint.bitcoin").allowed
    assert not guard.check_capability("fs.read").allowed


# ──────────────────────────────────────────────────────────────────
# AuditTrail — hash-chained immutable log
# ──────────────────────────────────────────────────────────────────
def test_audit_trail_append_and_verify(tmp_path):
    audit = AuditTrail(tmp_path / "audit.db")
    audit.append(AuthDecision(allowed=True, reason="test", tool="fs.read", user="alice"))
    audit.append(AuthDecision(allowed=False, reason="denied", tool="fs.read", user="bob"))
    valid, broken_at = audit.verify_chain()
    assert valid
    assert broken_at == 0


def test_audit_trail_detects_tampering(tmp_path):
    audit = AuditTrail(tmp_path / "audit.db")
    audit.append(AuthDecision(allowed=True, reason="ok", tool="fs.read", user="alice"))
    audit.append(AuthDecision(allowed=True, reason="ok", tool="fs.read", user="bob"))
    # Tamper: directly modify the JSON payload (the hash input)
    audit._conn.execute(
        "UPDATE audit SET payload = replace(payload, '\"reason\": \"ok\"', "
        "'\"reason\": \"HACKED\"') WHERE seq=1"
    )
    audit._conn.commit()
    valid, broken_at = audit.verify_chain()
    assert not valid
    assert broken_at == 1


def test_audit_trail_query(tmp_path):
    audit = AuditTrail(tmp_path / "audit.db")
    audit.append(AuthDecision(allowed=True, reason="ok", tool="fs.read", user="alice"))
    audit.append(AuthDecision(allowed=False, reason="deny", tool="fs.write", user="bob"))
    allow = audit.query(decision="allow")
    deny = audit.query(decision="deny")
    assert len(allow) == 1
    assert len(deny) == 1
    assert deny[0]["user"] == "bob"
