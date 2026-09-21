"""Native code tools for ODC v4 — the OpenCode capabilities, in-process.

These are the tools an LLM uses for software engineering tasks. They
all run inside the ODC v4 process; no subprocess, no npm, no HTTP.
The Python implementation covers everything an LLM actually needs
for "read code, edit code, plan, track, verify":

  Reading:
    code.read       - read file with extracted symbols (Python AST
                      for .py, regex fallback for other langs)
    code.glob       - find files by name
    code.grep       - regex search across the project

  Editing:
    code.write      - write/overwrite a file
    code.edit       - find/replace, must be unique
    code.multi_edit - atomic edits across N files in one call

  Project intelligence:
    code.symbols    - list functions/classes/methods of a file
    code.references - find where a symbol is used

  Working state:
    code.todo_add   - add a todo item
    code.todo_update - change status / content
    code.todo_list  - read the todo list
    code.todo_clear - wipe the list (start fresh)

  Verification:
    code.diff       - unified diff of a file vs a saved snapshot

The previous "fs.*" tools still work (they're simpler, faster). The
"code.*" tools are heavier but project-aware. The LLM picks based
on what the task needs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from odc.code.diff import file_diff, unified_diff
from odc.code.symbols import extract_symbols, find_references
from odc.code.todos import get_store
from odc.tools.base import tool


# ---- Reading ---------------------------------------------------------------


@tool(
    name="code.read",
    description=(
        "Read a file from the project. Returns the file text plus, "
        "for source files, the list of symbols (functions, classes, "
        "methods) defined in it — so the LLM can navigate the code "
        "without re-parsing. Python files use the AST; other "
        "languages use a regex-based fallback."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file. Relative paths resolve from cwd.",
            },
            "offset": {
                "type": "integer",
                "description": "Start at this line (0-based).",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Read at most this many lines. Default: whole file.",
                "default": None,
            },
            "include_symbols": {
                "type": "boolean",
                "description": "Include extracted symbols in the response. Default true.",
                "default": True,
            },
        },
        "required": ["path"],
    },
)
async def code_read(
    path: str,
    offset: int = 0,
    limit: int | None = None,
    include_symbols: bool = True,
) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"no such file: {p}")
    if not p.is_file():
        raise IsADirectoryError(f"not a file: {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)
    end = total if limit is None else min(offset + limit, total)
    sliced = "\n".join(lines[offset:end])
    out: dict[str, Any] = {
        "path": str(p),
        "total_lines": total,
        "start": offset,
        "end": end,
        "text": sliced,
    }
    if include_symbols:
        out["symbols"] = extract_symbols(str(p), text=text)
    return out


@tool(
    name="code.glob",
    description=(
        "Find files by name pattern. Same semantics as Path.rglob "
        "with shell-style globs. Returns paths relative to root. "
        "Default root is the current directory."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'.",
            },
            "root": {
                "type": "string",
                "description": "Root directory to search. Default: cwd.",
                "default": ".",
            },
            "limit": {
                "type": "integer",
                "description": "Max results. Default 200.",
                "default": 200,
            },
        },
        "required": ["pattern"],
    },
)
async def code_glob(pattern: str, root: str = ".", limit: int = 200) -> list[str]:
    base = Path(root).expanduser()
    if not base.exists():
        raise FileNotFoundError(f"root does not exist: {base}")
    matches = sorted(str(p.relative_to(base)) for p in base.glob(pattern) if p.is_file())
    return matches[: max(0, limit)]


@tool(
    name="code.grep",
    description=(
        "Search a directory for a regex pattern. Uses ripgrep (rg) "
        "when available for speed; falls back to a Python walker. "
        "Returns matches as {path, line, text, context_before, "
        "context_after}. Respects .gitignore."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search. Default: cwd.",
                "default": ".",
            },
            "glob": {
                "type": "string",
                "description": "File glob, e.g. '*.py'. Default: all files.",
                "default": None,
            },
            "context": {
                "type": "integer",
                "description": "Number of context lines before/after each match. Default 0.",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Max results. Default 100.",
                "default": 100,
            },
        },
        "required": ["pattern"],
    },
)
async def code_grep(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    context: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    import re
    from shutil import which

    base = Path(path).expanduser()
    if not base.exists():
        raise FileNotFoundError(f"path does not exist: {base}")

    # Prefer ripgrep for speed.
    if which("rg"):
        return await _rg_grep(pattern, base, glob, context, limit)

    # Python fallback.
    rx = re.compile(pattern)
    out: list[dict[str, Any]] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if glob and not p.match(glob):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not rx.search(line):
                continue
            ctx_before = lines[max(0, i - context) : i]
            ctx_after = lines[i + 1 : i + 1 + context]
            out.append(
                {
                    "path": str(p.relative_to(base)) if base in p.parents else str(p),
                    "line": i + 1,
                    "text": line[:300],
                    "context_before": ctx_before,
                    "context_after": ctx_after,
                }
            )
            if len(out) >= limit:
                return out
    return out


async def _rg_grep(
    pattern: str,
    base: Path,
    glob: str | None,
    context: int,
    limit: int,
) -> list[dict[str, Any]]:
    import asyncio
    import json

    cmd = [
        "rg",
        "--no-heading",
        "--line-number",
        "--color=never",
        "--max-columns=300",
    ]
    if context:
        cmd += [f"-C", str(context)]
    if glob:
        cmd += ["--glob", glob]
    cmd += ["--json", pattern, str(base)]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode not in (0, 1):  # 1 = no matches
        raise RuntimeError(f"rg error: {stderr.decode()[:500]}")

    out: list[dict[str, Any]] = []
    for line in stdout.decode(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "match":
            continue
        d = ev["data"]
        path = d["path"]["text"]
        line_no = d["line_number"]
        text = d["lines"].get("text", "").rstrip()
        out.append(
            {
                "path": path,
                "line": line_no,
                "text": text,
                "context_before": [],
                "context_after": [],
            }
        )
        if len(out) >= limit:
            break
    return out


# ---- Editing ---------------------------------------------------------------


@tool(
    name="code.write",
    description=(
        "Write a string to a file, creating parent directories as "
        "needed. Overwrites the file. Side-effect: requires confirm."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Destination file path."},
            "content": {"type": "string", "description": "Full file contents to write."},
        },
        "required": ["path", "content"],
    },
    side_effect=True,
    requires_confirm=True,
)
async def code_write(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {p}"


@tool(
    name="code.edit",
    description=(
        "Replace an exact, unique substring in a file. Fails if the "
        "snippet is not found or is not unique. Use this for surgical "
        "edits; use code.write for full-file rewrites. Side-effect: "
        "requires confirm."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to edit."},
            "old_text": {
                "type": "string",
                "description": "Exact substring to replace. Must appear exactly once.",
            },
            "new_text": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_text", "new_text"],
    },
    side_effect=True,
    requires_confirm=True,
)
async def code_edit(path: str, old_text: str, new_text: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(p)
    content = p.read_text(encoding="utf-8")
    count = content.count(old_text)
    if count == 0:
        raise ValueError(f"old_text not found in {p}")
    if count > 1:
        raise ValueError(
            f"old_text appears {count} times in {p} — must be unique. "
            f"Provide more surrounding context."
        )
    new_content = content.replace(old_text, new_text, 1)
    p.write_text(new_content, encoding="utf-8")
    return f"edited {p}: replaced {len(old_text)} chars with {len(new_text)}"


@tool(
    name="code.multi_edit",
    description=(
        "Apply multiple edits across multiple files atomically. "
        "If any edit fails, all previous edits in the batch are "
        "rolled back. Each edit's old_text must be unique within "
        "its file. Side-effect: requires confirm."
    ),
    parameters={
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": "List of {path, old_text, new_text} objects.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        },
        "required": ["edits"],
    },
    side_effect=True,
    requires_confirm=True,
)
async def code_multi_edit(edits: list[dict[str, str]]) -> dict[str, Any]:
    """Apply edits in order, rolling back on the first failure."""
    if not edits:
        return {"applied": 0, "rolled_back": 0, "results": []}
    backups: list[tuple[Path, str]] = []
    results: list[dict[str, Any]] = []
    try:
        for e in edits:
            p = Path(e["path"]).expanduser()
            if not p.exists():
                raise FileNotFoundError(f"no such file: {p}")
            original = p.read_text(encoding="utf-8")
            count = original.count(e["old_text"])
            if count == 0:
                raise ValueError(f"old_text not found in {p}")
            if count > 1:
                raise ValueError(
                    f"old_text appears {count} times in {p} — must be unique"
                )
            # All preconditions met: back up, then apply.
            backups.append((p, original))
            new_content = original.replace(e["old_text"], e["new_text"], 1)
            p.write_text(new_content, encoding="utf-8")
            results.append(
                {
                    "path": str(p),
                    "ok": True,
                    "replaced": len(e["old_text"]),
                    "with": len(e["new_text"]),
                }
            )
    except Exception as e:
        # Roll back everything we already applied.
        for p, content in backups:
            try:
                p.write_text(content, encoding="utf-8")
            except OSError:
                pass
        return {
            "applied": len(results),
            "rolled_back": len(backups),
            "results": results,
            "error": f"{type(e).__name__}: {e}",
        }
    return {"applied": len(results), "rolled_back": 0, "results": results}


# ---- Project intelligence --------------------------------------------------


@tool(
    name="code.symbols",
    description=(
        "Extract the symbols (functions, classes, methods, module-level "
        "vars) from a source file. For Python this uses the AST and "
        "includes docstrings + arg lists. For other languages it's a "
        "regex-based fallback. Use this instead of code.read when you "
        "want the shape of the file without its full text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to inspect."},
        },
        "required": ["path"],
    },
)
async def code_symbols(path: str) -> list[dict[str, Any]]:
    return extract_symbols(path)


@tool(
    name="code.references",
    description=(
        "Find references to a symbol across the project. Returns "
        "{path, line, text} for each match. This is a lexical search "
        "(whole-word), not a real LSP references query — it covers "
        "the obvious cases and may also pick up unrelated mentions "
        "in strings or comments. Treat the output as a hint."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Symbol name to search for (whole-word match).",
            },
            "root": {
                "type": "string",
                "description": "Project root. Default: cwd.",
                "default": ".",
            },
            "glob": {
                "type": "string",
                "description": "File glob, e.g. '*.py'.",
                "default": None,
            },
            "limit": {
                "type": "integer",
                "description": "Max results. Default 200.",
                "default": 200,
            },
        },
        "required": ["name"],
    },
)
async def code_references(
    name: str, root: str = ".", glob: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    return find_references(name, root=root, glob=glob, limit=limit)


# ---- Todos ------------------------------------------------------------------


@tool(
    name="code.todo_add",
    description=(
        "Add an item to the in-memory todo list. Use this to plan a "
        "multi-step coding task before executing it. Todos are "
        "session-only — they don't persist after the agent ends. Use "
        "memory.save for things that should survive across sessions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "What to do. One short sentence.",
            },
            "priority": {
                "type": "string",
                "description": "'low' | 'normal' | 'high'. Default 'normal'.",
                "default": "normal",
            },
        },
        "required": ["content"],
    },
)
async def code_todo_add(content: str, priority: str = "normal") -> dict[str, Any]:
    tid = get_store().add(content=content, priority=priority)
    return get_store().get(tid).to_dict()


@tool(
    name="code.todo_update",
    description=(
        "Update a todo's status, content, priority, or notes. "
        "Set status='done' to mark an item complete; the tool auto-"
        "stamps the completion time."
    ),
    parameters={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Todo id (from code.todo_add)."},
            "status": {
                "type": "string",
                "description": "New status: 'pending' | 'in_progress' | 'done' | 'cancelled'.",
                "default": None,
            },
            "content": {"type": "string", "description": "Replace the content.", "default": None},
            "priority": {
                "type": "string",
                "description": "New priority: 'low' | 'normal' | 'high'.",
                "default": None,
            },
            "notes": {"type": "string", "description": "Append a free-form note.", "default": None},
        },
        "required": ["id"],
    },
)
async def code_todo_update(
    id: str,
    status: str | None = None,
    content: str | None = None,
    priority: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if status is not None:
        fields["status"] = status
    if content is not None:
        fields["content"] = content
    if priority is not None:
        fields["priority"] = priority
    if notes is not None:
        fields["notes"] = notes
    return get_store().update(id, **fields).to_dict()


@tool(
    name="code.todo_list",
    description=(
        "Read the current todo list. Optionally filter by status or "
        "priority. Items are sorted: in_progress first, then by "
        "priority (high first), then by creation time."
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Filter by status. Default: all.",
                "default": None,
            },
            "priority": {
                "type": "string",
                "description": "Filter by priority. Default: all.",
                "default": None,
            },
        },
    },
)
async def code_todo_list(
    status: str | None = None, priority: str | None = None
) -> dict[str, Any]:
    items = [t.to_dict() for t in get_store().list(status=status, priority=priority)]
    return {"summary": get_store().summary(), "items": items}


@tool(
    name="code.todo_clear",
    description=(
        "Wipe the in-memory todo list. Use this when starting a fresh "
        "task and you want to discard the previous plan."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
async def code_todo_clear() -> dict[str, Any]:
    n = get_store().clear()
    return {"cleared": n}


# ---- Verification ----------------------------------------------------------


@tool(
    name="code.diff",
    description=(
        "Show a unified diff for a file. If `against` is given, "
        "compare the current file against that path. If not, this is "
        "a no-op (the file's current text is returned but no diff). "
        "Use this to verify a coding task actually changed the file."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Current file path."},
            "against": {
                "type": "string",
                "description": "Path to a saved baseline (e.g. before the edit).",
                "default": None,
            },
        },
        "required": ["path"],
    },
)
async def code_diff(path: str, against: str | None = None) -> dict[str, Any]:
    return file_diff(path, against=against)


# ---- Registry helper ------------------------------------------------------


def all_code_tools() -> list:
    """Return every code.* tool, in registration order.

    Order matters slightly: edit/write first (most used), then read,
    then analysis, then todos, then verification.
    """
    return [
        code_read,
        code_glob,
        code_grep,
        code_symbols,
        code_references,
        code_write,
        code_edit,
        code_multi_edit,
        code_todo_add,
        code_todo_update,
        code_todo_list,
        code_todo_clear,
        code_diff,
    ]
