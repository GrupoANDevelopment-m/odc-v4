"""File-system tools: read, write, edit, list.

`write` and `edit` are side-effecting and require confirm=True unless the
target path is inside ODC_DATA_DIR (the agent's own sandbox).
"""
from __future__ import annotations

from pathlib import Path

from odc.tools.base import Tool, tool


@tool(
    name="fs.read",
    description=(
        "Read a text file from disk. Returns the file contents (truncated to "
        "~8KB). For large files, use a search tool first to find the right region."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative file path."},
            "max_bytes": {
                "type": "integer",
                "description": "Cap output size. Default 65536.",
                "default": 65536,
            },
        },
        "required": ["path"],
    },
)
async def read_file(path: str, max_bytes: int = 65536) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"No such file: {p}")
    if not p.is_file():
        raise IsADirectoryError(f"Not a file: {p}")
    raw = p.read_bytes()[:max_bytes]
    return raw.decode("utf-8", errors="replace")


@tool(
    name="fs.list",
    description="List entries in a directory. Hidden files included unless excluded.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path."},
            "pattern": {
                "type": "string",
                "description": "Optional glob, e.g. '*.py'.",
                "default": None,
            },
            "max_entries": {
                "type": "integer",
                "description": "Cap number of entries returned. Default 200.",
                "default": 200,
            },
        },
        "required": ["path"],
    },
)
async def list_dir(path: str, pattern: str | None = None, max_entries: int = 200) -> list[str]:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"No such directory: {p}")
    if not p.is_dir():
        raise NotADirectoryError(f"Not a directory: {p}")
    if pattern:
        items = sorted(p.glob(pattern))
    else:
        items = sorted(p.iterdir())
    names = [str(x) for x in items[:max_entries]]
    return names


@tool(
    name="fs.write",
    description=(
        "Write a string to a file, creating parent directories as needed. "
        "Overwrites the file. Side-effect: requires confirm=True."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Destination file path."},
            "content": {"type": "string", "description": "Full file contents to write."},
        },
        "required": ["path", "content"],
    },
    requires_confirm=True,
    side_effect=True,
)
async def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {p}"


@tool(
    name="fs.edit",
    description=(
        "Replace an exact, unique substring in a file. Fails if the snippet "
        "is not found or is not unique. Side-effect: requires confirm=True."
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
    requires_confirm=True,
    side_effect=True,
)
async def edit_file(path: str, old_text: str, new_text: str) -> str:
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
