"""Visibility endpoints — capabilities, decisions, cognition state, upload.

This module exposes the agent's internals for transparency:

  GET  /api/capabilities         — every tool + skill + MCP organized
  GET  /api/decisions            — black-box decision log (audit trail)
  POST /api/decisions            — filter black-box by tool/time/allowed
  GET  /api/cognition/state      — current cognitive state snapshot
  POST /api/upload               — upload any file up to 500MB

These power the /#/visibility subpage in the web UI.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from odc.observability import get_logger

log = get_logger("odc.visibility")


# ── capabilities ───────────────────────────────────────────────
def _build_tool_inventory() -> dict[str, Any]:
    """Build complete inventory of all tools, skills, MCPs."""
    from odc.config import Config
    cfg = Config()
    cfg.ensure_dirs()
    from odc import Agent
    agent = Agent(config=cfg, auto_approve=True, interactive=False)

    tools = []
    for name in sorted(agent.tool_names()):
        try:
            tool = agent.tools.get(name)
            sig = getattr(tool, "sign", None) if tool else None
            params_keys = []
            if sig and hasattr(sig, "get") and sig.get("parameters"):
                props = sig["parameters"].get("properties") or {}
                params_keys = list(props.keys())
            tools.append({
                "name": name,
                "category": name.split(".")[0] if "." in name else "other",
                "description": (getattr(tool, "description", "") if tool else "")[:160],
                "side_effect": bool(getattr(sig, "get", lambda *_: False)("side_effect")) if sig else False,
                "params": params_keys[:8],
            })
        except Exception as e:
            tools.append({"name": name, "category": "?", "error": str(e)[:80]})

    # Group by category
    by_cat: dict[str, list] = {}
    for t in tools:
        by_cat.setdefault(t["category"], []).append(t)

    # Skills
    skills = []
    for s in (getattr(agent, "skills", []) or []):
        skills.append({
            "name": getattr(s, "name", "unknown"),
            "description": (getattr(s, "description", "") or "")[:160],
            "triggers": list(getattr(s, "triggers", []) or [])[:5],
        })

    # MCP tools (tools whose name starts with "osiris.")
    mcps = [t for t in tools if t["category"] == "osiris"]

    # Dynamic tools / skills on disk
    dyn_paths = cfg.data_dir / "dynamic"
    dyn_tools = []
    dyn_skills = []
    if (dyn_paths / "tools").exists():
        for p in sorted((dyn_paths / "tools").glob("*.py")):
            if p.name == "__init__.py":
                continue
            dyn_tools.append({"name": p.stem, "path": str(p),
                              "size": p.stat().st_size})
    if (dyn_paths / "skills").exists():
        for p in sorted((dyn_paths / "skills").glob("*.py")):
            if p.name == "__init__.py":
                continue
            dyn_skills.append({"name": p.stem, "path": str(p),
                                "size": p.stat().st_size})

    return {
        "total": len(tools),
        "tools": tools,
        "categories": {k: len(v) for k, v in by_cat.items()},
        "skills": skills,
        "mcps": mcps,
        "dynamic_tools": dyn_tools,
        "dynamic_skills": dyn_skills,
    }


def get_capabilities() -> dict[str, Any]:
    """Public entry point — returns inventory."""
    try:
        return _build_tool_inventory()
    except Exception as e:
        log.exception("capabilities failed")
        return {"error": str(e), "total": 0, "tools": [], "skills": [],
                "mcps": [], "dynamic_tools": [], "dynamic_skills": []}


# ── decisions (black box audit log) ────────────────────────────
def get_decisions(limit: int = 50, tool: str | None = None,
                   only_allowed: bool | None = None) -> dict[str, Any]:
    """Read audit trail decisions. Filterable."""
    from odc.config import Config
    cfg = Config()
    audit_path = cfg.data_dir / "audit" / "audit.db"
    if not audit_path.exists():
        return {"decisions": [], "total": 0, "note": "no audit trail yet"}

    import sqlite3
    sql = "SELECT seq, ts, user_id, tool, allowed, payload, hash FROM audit"
    where = []
    args: list = []
    if tool:
        where.append("tool = ?")
        args.append(tool)
    if only_allowed is True:
        where.append("allowed = 1")
    elif only_allowed is False:
        where.append("allowed = 0")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY seq DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))

    try:
        con = sqlite3.connect(str(audit_path))
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, args).fetchall()
        # Total without limit
        total_sql = "SELECT COUNT(*) FROM audit"
        if where:
            total_sql += " WHERE " + " AND ".join(where[:-0])  # safe copy
        # easier:
        total = con.execute(
            "SELECT COUNT(*) FROM audit" + (" WHERE " + " AND ".join(where) if where else ""),
            args[:-1] if len(args) > 1 else []
        ).fetchone()[0]
        con.close()
    except Exception as e:
        return {"error": str(e), "decisions": [], "total": 0}

    decisions = []
    for r in rows:
        try:
            payload = json.loads(r["payload"]) if r["payload"] else {}
        except Exception:
            payload = {"_raw": r["payload"][:200]}
        decisions.append({
            "seq": r["seq"],
            "ts": r["ts"],
            "user": r["user_id"],
            "tool": r["tool"],
            "allowed": bool(r["allowed"]),
            "hash": r["hash"][:16] + "..." if r["hash"] else "",
            "payload": payload,
        })
    return {"decisions": decisions, "total": total, "returned": len(decisions)}


# ── cognition state (live reasoning) ───────────────────────────
def get_cognition_state() -> dict[str, Any]:
    """Snapshot of the agent's cognitive state right now."""
    from odc.config import Config
    cfg = Config()
    cog_dir = cfg.data_dir / "cognitive"

    profile = None
    if (cog_dir / "profile.json").exists():
        try:
            profile = json.loads((cog_dir / "profile.json").read_text())
        except Exception:
            pass

    insights = []
    insights_path = cog_dir / "insights.jsonl"
    if insights_path.exists():
        for line in insights_path.read_text(errors="replace").splitlines()[-30:]:
            try:
                insights.append(json.loads(line))
            except Exception:
                continue

    heuristics = []
    hp = cog_dir / "heuristics.jsonl"
    if hp.exists():
        for line in hp.read_text(errors="replace").splitlines()[-20:]:
            try:
                heuristics.append(json.loads(line))
            except Exception:
                continue

    # Reflections
    reflections = []
    rp = cog_dir / "reflections.jsonl"
    if rp.exists():
        for line in rp.read_text(errors="replace").splitlines()[-15:]:
            try:
                reflections.append(json.loads(line))
            except Exception:
                continue

    return {
        "profile": profile,
        "recent_insights": insights[-15:],
        "heuristics_count": len(heuristics),
        "recent_heuristics": heuristics[-10:],
        "recent_reflections": reflections[-10:],
        "path": str(cog_dir),
    }


# ── file upload (multipart, 500MB cap) ─────────────────────────
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

def save_upload(content: bytes, filename: str, content_type: str) -> dict[str, Any]:
    """Save uploaded file to <data_dir>/uploads/. Returns metadata.

    - Validates size (≤ 500MB)
    - Sanitizes filename (no path traversal)
    - Writes file with UUID prefix to avoid collisions
    """
    if len(content) > MAX_UPLOAD_BYTES:
        return {
            "ok": False,
            "error": f"file too large: {len(content):,} bytes (max {MAX_UPLOAD_BYTES:,})",
        }

    # Sanitize filename — strip path, keep basename
    safe_name = re.sub(r"[^\w.\-]", "_", Path(filename).name)
    if not safe_name or safe_name.startswith("."):
        safe_name = f"upload_{int(time.time())}{Path(safe_name).suffix}"

    # UUID prefix to avoid collisions, preserve original extension
    uid = uuid.uuid4().hex[:12]
    final_name = f"{uid}_{safe_name}"

    uploads_dir = (getattr(config, "data_dir", Path("./data")) if (config := _cfg()) else Path("./data")) / "uploads"
    uploads_dir = Path(uploads_dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    target = uploads_dir / final_name
    target.write_bytes(content)
    return {
        "ok": True,
        "path": str(target),
        "size": len(content),
        "filename": final_name,
        "original_name": filename,
        "content_type": content_type,
        "uploaded_at": time.time(),
    }


def _cfg():
    try:
        from odc.config import Config
        return Config()
    except Exception:
        return None


def list_uploads() -> dict[str, Any]:
    """List currently uploaded files."""
    cfg = _cfg()
    if cfg is None:
        return {"uploads": []}
    uploads_dir = cfg.data_dir / "uploads"
    if not uploads_dir.exists():
        return {"uploads": []}
    out = []
    for p in sorted(uploads_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file():
            out.append({
                "filename": p.name,
                "size": p.stat().st_size,
                "uploaded_at": p.stat().st_mtime,
            })
    return {"uploads": out, "total": len(out),
            "max_size": MAX_UPLOAD_BYTES}


# ── parse multipart/form-data (stdlib only) ─────────────────────
def parse_multipart(headers: dict[str, str], body: bytes,
                    boundary: str) -> dict[str, Any]:
    """Tiny multipart parser — just enough for file uploads.

    Returns {"fields": {...}, "files": [{"name", "filename", "content_type", "data"}, ...]}

    Schema:
      --boundary\r\n
      Content-Disposition: form-data; name="<field>"\r\n\r\n<value>\r\n
      --boundary\r\n
      Content-Disposition: form-data; name="<field>"; filename="<filename>"\r\n
      Content-Type: <type>\r\n\r\n<binary data>\r\n
      --boundary--\r\n
    """
    sep = b"--" + boundary.encode("ascii")
    # Split on boundary; each part starts with \r\n after the boundary
    parts_raw = body.split(sep)
    fields: dict[str, str] = {}
    files: list[dict] = []
    for part_raw in parts_raw:
        if not part_raw or part_raw == b"\r\n":
            continue
        if part_raw == b"--\r\n" or part_raw == b"--":
            continue
        # Strip the leading \r\n
        if part_raw.startswith(b"\r\n"):
            part_raw = part_raw[2:]
        # Strip trailing \r\n before next boundary or --
        if part_raw.endswith(b"\r\n"):
            part_raw = part_raw[:-2]
        # split headers / body
        try:
            header_blob, data = part_raw.split(b"\r\n\r\n", 1)
        except ValueError:
            continue
        headers_str = header_blob.decode("ascii", errors="replace")
        h = {}
        for line in headers_str.split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                h[k.strip().lower()] = v.strip()
        cd = h.get("content-disposition", "")
        # parse name
        name_m = re.search(r'name="([^"]+)"', cd)
        filename_m = re.search(r'filename="([^"]+)"', cd)
        ct = h.get("content-type", "application/octet-stream")
        if filename_m:
            files.append({
                "name": name_m.group(1) if name_m else "file",
                "filename": filename_m.group(1),
                "content_type": ct,
                "data": data,
            })
        elif name_m:
            fields[name_m.group(1)] = data.decode("utf-8", errors="replace")
    return {"fields": fields, "files": files}


def find_boundary(content_type: str) -> str | None:
    m = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type)
    if m:
        return m.group(1) or m.group(2)
    return None
