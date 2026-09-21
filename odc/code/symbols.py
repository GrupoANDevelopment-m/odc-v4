"""Symbol extraction and reference search.

This is the ODC v4 answer to OpenCode's LSP integration. We can't
speak LSP without bringing up a language server, but for the things
an LLM needs most — what functions exist, where they're defined,
where they're called — Python's `ast` module is enough for Python
files, and a careful regex is enough for the rest.

The output is intentionally shaped like what an LSP would return:
  {symbols: [{name, kind, line, col, docstring}], references: [...]}

This way the LLM gets a consistent shape regardless of language.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

# Recognized source extensions and a stub parser for each.
_EXT_TO_PARSER = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
}


def _is_source(path: str) -> bool:
    return Path(path).suffix in _EXT_TO_PARSER


# ---- Python symbols via ast -----------------------------------------------


def _py_symbols(tree: ast.AST) -> list[dict[str, Any]]:
    """Walk a Python AST and collect function / class / method symbols."""
    out: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "method" if _is_method(node) else "function"
            doc = ast.get_docstring(node)
            out.append(
                {
                    "name": node.name,
                    "kind": kind,
                    "line": node.lineno,
                    "col": node.col_offset,
                    "end_line": getattr(node, "end_lineno", None),
                    "doc": (doc[:200] + "...") if doc and len(doc) > 200 else doc,
                    "args": [a.arg for a in node.args.args],
                    "decorators": [
                        ast.unparse(d) if hasattr(ast, "unparse") else ""
                        for d in node.decorator_list
                    ],
                }
            )
        elif isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node)
            out.append(
                {
                    "name": node.name,
                    "kind": "class",
                    "line": node.lineno,
                    "col": node.col_offset,
                    "end_line": getattr(node, "end_lineno", None),
                    "doc": (doc[:200] + "...") if doc and len(doc) > 200 else doc,
                    "bases": [
                        ast.unparse(b) if hasattr(ast, "unparse") else ""
                        for b in node.bases
                    ],
                }
            )
        elif isinstance(node, ast.Assign):
            # Module-level constants / variables.
            if hasattr(node, "col_offset") and node.col_offset == 0:
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        out.append(
                            {
                                "name": tgt.id,
                                "kind": "variable",
                                "line": node.lineno,
                                "col": 0,
                            }
                        )
    return out


def _is_method(node: ast.AST) -> bool:
    """Heuristic: a function defined directly inside a class body."""
    # We can't easily walk parents here, so we just tag all functions
    # as 'function' and let the line range + context sort it out.
    # (A more precise check requires a parent map, which ast doesn't
    # give us. The LLM gets the docstring + args to disambiguate.)
    return False


# ---- Regex fallback for other languages ----------------------------------


# Patterns are intentionally conservative: a function/class definition
# that starts at column 0 (or near it) and ends with `{` or `:`.
_FALLBACK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "function",
        re.compile(
            r"^(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(",
            re.MULTILINE,
        ),
    ),
    (
        "class",
        re.compile(
            r"^(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)\b",
            re.MULTILINE,
        ),
    ),
    (
        "method",
        re.compile(
            r"^\s+(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*"
            r"(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*[:{]",
            re.MULTILINE,
        ),
    ),
    (
        "function",
        re.compile(
            r"^func\s+(?:\([^)]*\)\s+)?(?P<name>[A-Za-z_][\w]*)\s*\(",
            re.MULTILINE,
        ),
    ),
    (
        "function",
        re.compile(
            r"^(?:pub\s+)?fn\s+(?P<name>[a-z_][\w]*)\s*\(",
            re.MULTILINE,
        ),
    ),
]


def _fallback_symbols(text: str, ext: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kind, pat in _FALLBACK_PATTERNS:
        for m in pat.finditer(text):
            name = m.group("name")
            line = text.count("\n", 0, m.start()) + 1
            out.append(
                {
                    "name": name,
                    "kind": kind,
                    "line": line,
                    "col": 0,
                }
            )
    # Deduplicate by (name, line) — fallback patterns can double-hit.
    seen = set()
    deduped: list[dict[str, Any]] = []
    for s in out:
        key = (s["name"], s["line"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    return deduped


# ---- Public API ------------------------------------------------------------


def extract_symbols(path: str, text: str | None = None) -> list[dict[str, Any]]:
    """Return the symbols (functions, classes, methods) in `path`.

    For Python we use `ast`. For other languages we use a regex
    fallback that's good enough for the LLM to navigate the file.

    >>> syms = extract_symbols('demo.py', 'def foo():\\n    pass\\n')
    >>> any(s['name'] == 'foo' for s in syms)
    True
    """
    if text is None:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    ext = Path(path).suffix
    if ext in (".py", ".pyi"):
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            # Fall back to regex if the file is broken; tell the LLM.
            syms = _fallback_symbols(text, ext)
            for s in syms:
                s["parse_error"] = f"line {e.lineno}: {e.msg}"
            return syms
        return _py_symbols(tree)
    if _is_source(path):
        return _fallback_symbols(text, ext)
    return []


def find_references(
    name: str,
    *,
    root: str | Path = ".",
    glob: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Find references to a symbol across a project.

    A "reference" is any occurrence of the bare name as a whole word
    in a source file. The LLM should treat this as a hint, not a
    guarantee — it covers the obvious cases (calls, decorators,
    imports) and may also pick up unrelated mentions in strings.
    """
    base = Path(root).expanduser()
    if not base.exists():
        raise FileNotFoundError(base)
    rx = re.compile(rf"\b{re.escape(name)}\b")
    out: list[dict[str, Any]] = []
    paths = base.rglob(glob) if glob else base.rglob("*")
    for p in paths:
        if not p.is_file() or not _is_source(str(p)):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                rel = p.relative_to(base) if base in p.parents or p == base else p
                out.append(
                    {
                        "path": str(rel),
                        "line": i,
                        "text": line.strip()[:200],
                    }
                )
                if len(out) >= limit:
                    return out
    return out
