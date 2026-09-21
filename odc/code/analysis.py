"""Code analysis and replication tools.

Three new tools extend the code.* surface:

- code.analyze(path)         structural analysis of a file or directory
- code.system_map(root)       dependency graph across a project
- code.replicate(src, target) scaffold a new system at <target> based
                              on the structure of <src>

The point: the agent can now *understand* a system, not just read it,
and *reproduce* it elsewhere. This is the difference between "I can
read a file" and "I can build a clone of this architecture".
"""
from __future__ import annotations

import ast
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from odc.tools.base import tool


# ---------------------------------------------------------------------------
# code.analyze — structural analysis
# ---------------------------------------------------------------------------


def _analyze_python(path: Path) -> dict[str, Any]:
    """Analyze a single .py file: functions, classes, imports, patterns."""
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        return {
            "path": str(path),
            "language": "python",
            "error": f"SyntaxError: {e}",
            "loc": len(src.splitlines()),
        }

    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    imports: list[str] = []
    decorators: Counter = Counter()
    async_count = 0
    class_count = 0

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for n in ast.walk(node):
                if isinstance(n, ast.alias):
                    imports.append(n.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            async_count += isinstance(node, ast.AsyncFunctionDef)
            decs = [ast.unparse(d) for d in node.decorator_list]
            for d in decs:
                decorators[d.split("(")[0]] += 1
            functions.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "args": [a.arg for a in node.args.args],
                    "decorators": decs,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "returns": ast.unparse(node.returns) if node.returns else None,
                    "docstring": ast.get_docstring(node) or "",
                }
            )
        if isinstance(node, ast.ClassDef):
            class_count += 1
            methods = [
                n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            classes.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "bases": [ast.unparse(b) for b in node.bases],
                    "methods": methods,
                    "method_count": len(methods),
                }
            )

    return {
        "path": str(path),
        "language": "python",
        "loc": len(src.splitlines()),
        "function_count": len(functions),
        "class_count": class_count,
        "async_function_count": async_count,
        "imports": sorted(set(imports)),
        "top_decorators": dict(decorators.most_common(8)),
        "functions": functions[:30],
        "classes": classes[:20],
        "first_docstring": ast.get_docstring(tree) or "",
    }


def _analyze_directory(root: Path, max_files: int = 100) -> dict[str, Any]:
    """Analyze a directory of code: counts by language, total LOC, files."""
    counts: Counter = Counter()
    total_loc = 0
    files: list[dict[str, Any]] = []
    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "tsx", ".jsx": "jsx", ".go": "go", ".rs": "rust",
        ".java": "java", ".rb": "ruby", ".sh": "shell", ".md": "markdown",
    }
    paths = []
    for p in root.rglob("*"):
        if any(part in {"__pycache__", ".git", "node_modules", ".venv", "venv"} for part in p.parts):
            continue
        if p.is_file() and p.suffix in ext_map:
            paths.append(p)
    paths = sorted(paths)[:max_files]
    for p in paths:
        lang = ext_map[p.suffix]
        counts[lang] += 1
        try:
            loc = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        except Exception:
            loc = 0
        total_loc += loc
        files.append({"path": str(p.relative_to(root)), "language": lang, "loc": loc})
    return {
        "path": str(root),
        "file_count": len(paths),
        "total_loc": total_loc,
        "by_language": dict(counts),
        "files": files[:50],
    }


@tool(
    name="code.analyze",
    description="Structural analysis of a file or directory: functions, classes, imports, LOC, patterns.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory path."},
            "max_files": {"type": "integer", "description": "Max files for a directory.", "default": 100},
        },
        "required": ["path"],
    },
)
async def code_analyze(path: str, max_files: int = 100) -> dict[str, Any]:
    p = Path(path).resolve()
    if not p.exists():
        return {"ok": False, "error": f"path not found: {p}"}
    if p.is_file():
        if p.suffix == ".py":
            return {"ok": True, **(_analyze_python(p))}
        return {
            "ok": True,
            "path": str(p),
            "language": p.suffix.lstrip(".") or "unknown",
            "loc": len(p.read_text(encoding="utf-8", errors="replace").splitlines()),
            "note": "detailed analysis available only for .py files",
        }
    if p.is_dir():
        return {"ok": True, **(_analyze_directory(p, max_files=max_files))}
    return {"ok": False, "error": f"unsupported path type: {p}"}


# ---------------------------------------------------------------------------
# code.system_map — dependency graph
# ---------------------------------------------------------------------------


def _module_imports(path: Path) -> set[str]:
    """Return the set of top-level module names imported by a Python file."""
    out: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                out.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.add(node.module.split(".")[0])
    return out


@tool(
    name="code.system_map",
    description="Dependency map for a Python project: imports, most-depended-on modules, top importers.",
    parameters={
        "type": "object",
        "properties": {
            "root": {"type": "string", "description": "Project root.", "default": "."},
            "max_files": {"type": "integer", "description": "Max files to scan.", "default": 200},
        },
    },
)
async def code_system_map(root: str = ".", max_files: int = 200) -> dict[str, Any]:
    r = Path(root).resolve()
    if not r.exists():
        return {"ok": False, "error": f"root not found: {r}"}
    files = []
    for p in r.rglob("*.py"):
        if any(part in {"__pycache__", ".venv", "venv", "node_modules"} for part in p.parts):
            continue
        files.append(p)
    files = files[:max_files]
    edges: list[tuple[str, str]] = []
    indeg: Counter = Counter()
    outdeg: Counter = Counter()
    for f in files:
        mod = ".".join(f.relative_to(r).with_suffix("").parts)[:-9] if f.name == "__init__.py" else ".".join(f.relative_to(r).with_suffix("").parts)
        if mod.endswith("."):
            mod = mod[:-1]
        imports = _module_imports(f)
        for imp in imports:
            if imp.startswith("_") or imp in {"typing", "collections", "json", "os", "sys", "re", "time", "pathlib"}:
                continue
            edges.append((mod, imp))
            outdeg[mod] += 1
            indeg[imp] += 1
    top_imported = indeg.most_common(15)
    top_importer = outdeg.most_common(15)
    return {
        "ok": True,
        "root": str(r),
        "file_count": len(files),
        "edge_count": len(edges),
        "top_imported": [{"module": m, "imported_by_n_files": c} for m, c in top_imported],
        "top_importer": [{"module": m, "imports_n": c} for m, c in top_importer],
        "sample_edges": [
            {"from": a, "to": b} for a, b in edges[:30]
        ],
    }


# ---------------------------------------------------------------------------
# code.replicate — scaffold a clone of a system
# ---------------------------------------------------------------------------


_REPLICATE_TEMPLATE = '''"""Auto-generated scaffold based on {src_name}.

Created by `code.replicate` at {ts}. The original was analyzed with
`code.analyze`; the structure is mirrored here as a starting point.
Replace the placeholder functions with your real implementation.
"""

from __future__ import annotations

# --- Imports detected in the source ---
{imports}

# --- Module-level structure ---
{module_info}


# --- Placeholder functions (replace with your implementation) ---


async def run(task: str) -> dict:
    """Entry point. Mirror the source's `run(task)` interface."""
    return {{"ok": True, "task": task, "scaffold": True}}
'''


@tool(
    name="code.replicate",
    description="Generate a scaffold at <target> that mirrors the structure of <source> (imports, functions, classes).",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Path to source file."},
            "target": {"type": "string", "description": "Path to write the scaffold."},
        },
        "required": ["source", "target"],
    },
)
async def code_replicate(source: str, target: str) -> dict[str, Any]:
    src = Path(source).resolve()
    tgt = Path(target).resolve()
    if not src.exists():
        return {"ok": False, "error": f"source not found: {src}"}
    if not src.is_file() or src.suffix != ".py":
        return {"ok": False, "error": "source must be a single .py file (directories not yet supported)"}

    # Analyze the source
    info = _analyze_python(src)
    imports_block = "\n".join(f"# import {m}" for m in info.get("imports", [])[:20])
    fn_lines = []
    for f in info.get("functions", [])[:10]:
        sig_args = ", ".join(f.get("args", []))
        fn_lines.append(f"# def {f['name']}({sig_args}) -> {f.get('returns') or '?'}")
        if f.get("docstring"):
            doc_first = f["docstring"].splitlines()[0][:80]
            fn_lines.append(f"#   doc: {doc_first}")
    cls_lines = []
    for c in info.get("classes", [])[:5]:
        cls_lines.append(f"# class {c['name']}({', '.join(c.get('bases', []))}):  # {c['method_count']} methods")

    module_info = "\n".join(fn_lines + cls_lines) or "# (no functions or classes detected)"

    body = _REPLICATE_TEMPLATE.format(
        src_name=src.name,
        ts=time.strftime("%Y-%m-%d %H:%M:%S"),
        imports=imports_block or "# (no third-party imports)",
        module_info=module_info,
    )
    tgt.parent.mkdir(parents=True, exist_ok=True)
    tgt.write_text(body, encoding="utf-8")

    return {
        "ok": True,
        "source": str(src),
        "target": str(tgt),
        "source_loc": info.get("loc"),
        "mirrored_functions": len(info.get("functions", [])),
        "mirrored_classes": len(info.get("classes", [])),
        "imports_preserved": len(info.get("imports", [])),
        "scaffold_bytes": len(body),
    }


def all_analysis_tools() -> list:
    return [code_analyze, code_system_map, code_replicate]
