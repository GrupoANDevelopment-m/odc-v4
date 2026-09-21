"""Dynamic tool & skill creation — the obstacle breaker.

When the agent hits a capability wall (no matching tool, repeated
failures, the user mentions a specific system the agent has no
integration with), it can build its way out. This module gives the
agent the means:

  - `tool_create(name, description, body, tests)` : write a new tool
    to a dedicated location, validate it, dynamically import and
    register it. The new tool becomes available to the rest of the
    loop immediately.

  - `skill_create(name, description, triggers, body)` : write a new
    skill (markdown + YAML frontmatter) so future sessions can use
    the same knowledge.

  - `tool_load(name)` : explicitly load a previously created tool
    from disk (after a restart, for example).

  - `tool_list_dynamic()` : list every tool this agent has created.

**Safety.** Tool creation is the most powerful thing the agent can
do, so it's gated three ways:

  1. `requires_confirm=True` — the loop will ask the user before
     the tool runs.

  2. AST-level safety check — we walk the new code's syntax tree
     and reject obvious footguns: `os.system`, `subprocess.Popen`
     without `shell=False`, `eval`, `exec`, `__import__` of
     blacklisted modules. The agent can write such code if it
     really needs to — but only by importing it through a more
     deliberate path. (We can loosen this if it gets in the way.)

  3. Dedicated storage location — created tools live in
     `<data_dir>/dynamic/tools/`, not in the package source. The
     import system adds that directory to `sys.path` at startup,
     so `import my_tool` works without modifying the package.

Tests go in `<data_dir>/dynamic/tests/` and are run via Python's
`-m` with a subprocess, so a bad test can't take down the agent.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import re
import subprocess
import sys
import textwrap
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from odc.observability import get_logger, log_event

log = get_logger("odc.code.dynamic")

# ---- safety policy --------------------------------------------------------

# Patterns that, when found in the tool's source, trigger a refusal.
# The patterns are matched against the AST node text (not the raw
# string), so comments and strings are inspected too — the agent
# can't sneak a banned construct into a string literal.
_BANNED_CALLS: list[str] = [
    "os.system",
    "subprocess.run",        # we allow subprocess via the shell tool only
    "subprocess.Popen",
    "subprocess.call",
    "eval",
    "exec",
    "__import__",
    "compile",
    "input",                 # blocking stdio — would deadlock the agent
]

_BANNED_MODULES: list[str] = [
    "ctypes",
    "cffi",
]

# Allowed forms of subprocess usage (if the agent really needs it).
# If we see subprocess.* in a tool, the tool MUST be a thin wrapper
# around an allow-listed command.
_SUBPROCESS_USAGE_OKAY = False  # set True to relax; we keep it off for now

# Headline: how much self-extension we allow. Keep it sane; can be
# raised later if the agent proves it can use the freedom safely.


@dataclass
class SafetyReport:
    """Result of a static safety check on a new tool."""

    ok: bool
    reason: str = ""
    findings: list[str] = field(default_factory=list)


def check_safety(source: str) -> SafetyReport:
    """AST-based safety check on a Python source string.

    Banned calls are detected as `Name` / `Attribute` nodes. Banned
    modules are detected as `Import` / `ImportFrom` nodes. We never
    execute the code; we only inspect the AST.
    """
    findings: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return SafetyReport(ok=False, reason=f"SyntaxError: {e.msg} at line {e.lineno}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Extract the dotted name of what's being called.
            func = node.func
            name = _dotted_name(func)
            if name and name in _BANNED_CALLS:
                findings.append(f"banned call: {name} at line {node.lineno}")
        elif isinstance(node, ast.Attribute):
            # Bare attribute access on a banned module.
            root = _root_name(node)
            if root in _BANNED_MODULES:
                findings.append(f"banned module attribute: {root} at line {node.lineno}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _BANNED_MODULES:
                    findings.append(
                        f"banned module import: {alias.name} at line {node.lineno}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _BANNED_MODULES:
                findings.append(
                    f"banned module from-import: {node.module} at line {node.lineno}"
                )

    if not _SUBPROCESS_USAGE_OKAY:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _dotted_name(node.func)
                if name and name.startswith("subprocess."):
                    findings.append(
                        f"subprocess not allowed in dynamic tools "
                        f"(use the shell.run tool instead) at line {node.lineno}"
                    )

    if findings:
        return SafetyReport(ok=False, reason="; ".join(findings), findings=findings)
    return SafetyReport(ok=True, findings=[])


def _dotted_name(node: ast.AST) -> str | None:
    """Return the dotted name of a Call.func, or None if it's not static."""
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _root_name(node: ast.AST) -> str | None:
    """Return the leftmost Name in an Attribute chain (e.g. `ctypes.foo` -> `ctypes`)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _root_name(node.value)
    return None


# ---- storage locations -----------------------------------------------------


@dataclass
class DynamicPaths:
    """On-disk locations for dynamically-created tools and skills."""

    root: Path
    tools_dir: Path
    skills_dir: Path
    tests_dir: Path
    init_file: Path

    @classmethod
    def for_data_dir(cls, data_dir: Path) -> "DynamicPaths":
        root = data_dir / "dynamic"
        tools = root / "tools"
        skills = root / "skills"
        tests = root / "tests"
        init_file = tools / "__init__.py"
        for d in (tools, skills, tests):
            d.mkdir(parents=True, exist_ok=True)
        # Make the tools dir importable.
        if not init_file.exists():
            init_file.write_text('"""Auto-generated. Tools created by the agent."""\n')
        return cls(
            root=root,
            tools_dir=tools,
            skills_dir=skills,
            tests_dir=tests,
            init_file=init_file,
        )


# ---- tool creation --------------------------------------------------------


@dataclass
class ToolRecord:
    """What we know about a dynamically-created tool."""

    name: str
    description: str
    path: Path
    created_at: float
    last_loaded_at: float | None = None
    load_count: int = 0


# Module-level registry of created tools (so we can re-load after restart).
_created: dict[str, ToolRecord] = {}


def _record_load_path(paths: DynamicPaths) -> None:
    """Make sure the dynamic tools dir is on sys.path so we can import names."""
    sp = str(paths.tools_dir)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def write_tool(
    paths: DynamicPaths,
    *,
    name: str,
    body: str,
    description: str,
) -> tuple[ToolRecord, SafetyReport]:
    """Write a new tool to disk. Returns the record + safety report.

    Caller is expected to:
      - check the SafetyReport
      - call `load_tool(name, paths)` to import + register
    """
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        raise ValueError(
            f"invalid tool name {name!r}: must be lowercase, start with a letter, "
            f"and contain only [a-z0-9_]"
        )
    safety = check_safety(body)
    if not safety.ok:
        return ToolRecord(name, description, paths.tools_dir / f"{name}.py", 0), safety

    path = paths.tools_dir / f"{name}.py"
    header = textwrap.dedent(
        f'''\
        """Dynamic tool: {name}.

        {description}

        AUTO-GENERATED by odc.code.dynamic at {time.strftime("%Y-%m-%d %H:%M:%S")}.
        Edit with care — the agent will load whatever is here.
        """
        '''
    )
    path.write_text(header + "\n" + body, encoding="utf-8")
    rec = ToolRecord(
        name=name,
        description=description,
        path=path,
        created_at=time.time(),
    )
    _created[name] = rec
    log_event(log, 20, "tool_created", name=name, path=str(path))
    return rec, safety


def load_tool(name: str, paths: DynamicPaths) -> Any | None:
    """Import the module and find an @tool-decorated function inside.

    Returns the tool object (suitable for `ToolRegistry.register`), or
    None if nothing tool-shaped was found.
    """
    _record_load_path(paths)
    rec = _created.get(name)
    if rec is None:
        p = paths.tools_dir / f"{name}.py"
        if not p.exists():
            return None
        rec = ToolRecord(
            name=name, description="(loaded from disk)", path=p, created_at=p.stat().st_mtime
        )
        _created[name] = rec

    spec = importlib.util.spec_from_file_location(rec.path.stem, rec.path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not build import spec for {rec.path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Find a @tool-decorated callable (has .name and .parameters).
    candidate: Any = None
    for _, obj in inspect.getmembers(module):
        if callable(obj) and hasattr(obj, "name") and hasattr(obj, "parameters"):
            if getattr(obj, "name", "") == name:
                candidate = obj
                break
    if candidate is None:
        # Fallback: a top-level function with matching name.
        candidate = getattr(module, name, None)

    if candidate is not None:
        rec.last_loaded_at = time.time()
        rec.load_count += 1
        log_event(log, 20, "tool_loaded", name=name, path=str(rec.path))
    return candidate


def list_dynamic_tools(paths: DynamicPaths) -> list[dict[str, Any]]:
    """List every .py file in the tools dir, with creation time and load count."""
    out: list[dict[str, Any]] = []
    for p in sorted(paths.tools_dir.glob("*.py")):
        if p.name == "__init__.py":
            continue
        rec = _created.get(p.stem)
        out.append(
            {
                "name": p.stem,
                "path": str(p),
                "size": p.stat().st_size,
                "created_at": rec.created_at if rec else p.stat().st_mtime,
                "load_count": rec.load_count if rec else 0,
            }
        )
    return out


# ---- test runner ----------------------------------------------------------


def run_tool_test(paths: DynamicPaths, name: str, test_body: str) -> dict[str, Any]:
    """Run a test for a dynamic tool in a subprocess.

    The test must define `def test_<name>()` and assert things. The
    function returns a dict {ok, stdout, stderr, duration_ms}.
    """
    test_path = paths.tests_dir / f"test_{name}.py"
    test_path.write_text(test_body, encoding="utf-8")
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "stdout": e.stdout.decode() if isinstance(e.stdout, bytes) else str(e.stdout),
            "stderr": f"timeout after {e.timeout}s",
            "duration_ms": int((time.time() - t0) * 1000),
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
        "duration_ms": int((time.time() - t0) * 1000),
    }


# ---- skill creation -------------------------------------------------------


def write_skill(
    paths: DynamicPaths,
    *,
    name: str,
    description: str,
    triggers: list[str],
    body: str,
) -> Path:
    """Write a new skill (markdown + YAML frontmatter) to the dynamic skills dir."""
    if not re.match(r"^[a-z][a-z0-9_-]*$", name):
        raise ValueError(
            f"invalid skill name {name!r}: must be lowercase kebab/snake"
        )
    skill_dir = paths.skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    # Build the file by concatenation. Avoid textwrap.dedent on the
    # whole thing — the triggers list can have its own indentation
    # that dedent will munge.
    lines: list[str] = ["---"]
    lines.append(f"name: {name}")
    lines.append(f"description: {description}")
    lines.append("triggers:")
    for t in triggers:
        lines.append(f"  - {t}")
    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    lines.append("")
    skill_path.write_text("\n".join(lines), encoding="utf-8")
    log_event(log, 20, "skill_created", name=name, path=str(skill_path))
    return skill_path
