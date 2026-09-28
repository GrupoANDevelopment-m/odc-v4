"""The dynamic tools the agent uses to extend itself.

These are the @tool wrappers around `odc.code.dynamic`. They're
registered in the default tool registry like any other tool, so
the LLM can call them as `dynamic.tool_create`,
`dynamic.skill_create`, etc.

The shape follows what the obstacle_breaker skill teaches the
LLM to do: detect the wall, research, create, test, persist,
use.
"""
from __future__ import annotations

from typing import Any

from odc.code.dynamic import (
    DynamicPaths,
    SafetyReport,
    list_dynamic_tools,
    load_tool,
    run_tool_test,
    write_skill,
    write_tool,
)
from odc.tools.base import tool

# The current data dir is set by the agent at startup. The tools
# look it up on each call so they don't bake a path at import time.
_paths_ref: dict[str, Any] = {}


def set_dynamic_paths(paths: DynamicPaths) -> None:
    _paths_ref["paths"] = paths


def get_dynamic_paths() -> DynamicPaths:
    p = _paths_ref.get("paths")
    if p is None:
        raise RuntimeError(
            "Dynamic paths not configured. The agent constructs these "
            "automatically; if you're calling dynamic.* tools directly, "
            "call set_dynamic_paths() first."
        )
    return p


# ---- the tools ------------------------------------------------------------


@tool(
    name="dynamic.tool_create",
    description=(
        "Create a brand-new tool at runtime and make it available in "
        "the current session. The tool is a Python function decorated "
        "with `@tool(name=..., description=..., parameters={...})` "
        "from `odc.tools.base`. Use this when you need a capability "
        "that doesn't exist yet (corporate API, specific protocol, "
        "niche service). The body is the full Python source of the "
        "module. A safety check will reject `os.system`, `subprocess.*`, "
        "`eval`, `exec`, and a few other footguns — if you need a "
        "shell command, use the existing `shell.run` tool. Returns "
        "{ok, name, path, findings, error}. Side-effect: requires confirm."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Tool name. Lowercase, [a-z0-9_]. Becomes the tool id.",
            },
            "description": {
                "type": "string",
                "description": "One-line description of what the tool does. Goes in the tool spec.",
            },
            "body": {
                "type": "string",
                "description": (
                    "Full Python source for the module. Must define a function "
                    "decorated with @tool(name=..., description=..., parameters=...) "
                    "with a name matching the `name` parameter. Use `odc.tools.base.tool`."
                ),
            },
        },
        "required": ["name", "description", "body"],
    },
    side_effect=True,
    requires_confirm=True,
)
async def dynamic_tool_create(name: str, description: str, body: str) -> dict[str, Any]:
    # Auto-extension gate: respect user preference
    from odc.code.auto_extend import is_allowed
    if not is_allowed("dynamic.tool_create"):
        return {
            "ok": False,
            "error": "auto-extension disabled by user (Settings -> Auto-extension)",
            "name": name,
        }
    paths = get_dynamic_paths()
    rec, safety = write_tool(paths, name=name, body=body, description=description)
    if not safety.ok:
        return {
            "ok": False,
            "name": name,
            "path": str(rec.path),
            "error": f"safety check failed: {safety.reason}",
            "findings": safety.findings,
        }
    # Try to load it so it's available for the rest of the loop.
    tool_obj = load_tool(name, paths)
    if tool_obj is None:
        return {
            "ok": False,
            "name": name,
            "path": str(rec.path),
            "error": "wrote the file but couldn't import a @tool function from it",
        }
    # Register with the default registry if there is one.
    from odc.tools.base import ToolRegistry

    for reg in _registry_refs:
        try:
            reg.register(tool_obj)
        except ValueError:
            pass
    return {
        "ok": True,
        "name": name,
        "path": str(rec.path),
        "description": description,
        "loaded": True,
    }


@tool(
    name="dynamic.tool_load",
    description=(
        "Load a previously created tool from disk into the current "
        "session. Use this at the start of a session to re-attach "
        "tools you created in a previous run. The tool name must "
        "match the file in the dynamic tools directory."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Tool name to load."},
        },
        "required": ["name"],
    },
)
async def dynamic_tool_load(name: str) -> dict[str, Any]:
    from odc.code.auto_extend import is_allowed
    if not is_allowed("dynamic.tool_load"):
        return {
            "ok": False,
            "error": "auto-extension disabled (tool_load off)",
            "name": name,
        }
    paths = get_dynamic_paths()
    tool_obj = load_tool(name, paths)
    if tool_obj is None:
        return {"ok": False, "name": name, "error": "tool not found or no @tool function"}
    from odc.tools.base import ToolRegistry

    for reg in _registry_refs:
        try:
            reg.register(tool_obj)
        except ValueError:
            pass
    return {"ok": True, "name": name, "loaded": True}


# ---------------------------------------------------------------------------
# dynamic.tool_repair — overwrite an existing tool with a fixed version
# ---------------------------------------------------------------------------


@tool(
    name="dynamic.tool_repair",
    description=(
        "Repair an existing tool by overwriting its source with new "
        "Python code. Use this when a tool is broken (library renamed, "
        "API changed, sandbox restriction, etc). The new body must "
        "define a function decorated with @tool(name=<same_name>, ...). "
        "The tool is re-validated (AST safety), reloaded, and "
        "re-registered. Side-effect: modifies a file on disk."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the tool to repair."},
            "body": {"type": "string", "description": "New Python source for the tool."},
            "reason": {"type": "string", "description": "Short reason for the repair (logged)."},
        },
        "required": ["name", "body", "reason"],
    },
)
async def dynamic_tool_repair(name: str, body: str, reason: str) -> dict[str, Any]:
    from odc.code.auto_extend import is_allowed
    if not is_allowed("dynamic.tool_repair"):
        return {
            "ok": False,
            "error": "auto-extension disabled (tool_repair off)",
            "name": name,
        }
    paths = get_dynamic_paths()
    target = paths.tools_dir / f"{name}.py"
    if not target.exists():
        return {"ok": False, "error": f"no existing tool named {name!r} to repair"}
    # Write the new body
    rec, safety = write_tool(paths, name=name, body=body, description=f"repaired: {reason[:200]}")
    if not safety.ok:
        return {
            "ok": False,
            "error": f"safety check failed: {safety.reason}",
            "findings": safety.findings,
        }
    # Reload and re-register
    tool_obj = load_tool(name, paths)
    if tool_obj is None:
        return {"ok": False, "error": "wrote the file but couldn't import a @tool function from it"}
    from odc.tools.base import ToolRegistry
    for reg in _registry_refs:
        try:
            # Unregister old, register new
            try:
                reg.unregister(name)
            except (KeyError, AttributeError):
                pass
            reg.register(tool_obj)
        except Exception:
            pass
    # Reset the circuit breaker so a previously-broken tool gets
    # a fresh chance.
    from odc.resilience import get_breaker, CircuitState
    b = get_breaker(name)
    b.state = CircuitState.CLOSED
    b.failures = []
    b.opened_at = 0.0
    return {
        "ok": True,
        "name": name,
        "path": str(target),
        "repaired": True,
        "reason": reason,
        "circuit_reset": True,
    }


@tool(
    name="dynamic.tool_list",
    description=(
        "List every tool in the dynamic tools directory. Each entry "
        "has name, path, size, created_at, and load_count. Use this "
        "to find tools the agent has already built in past sessions."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
async def dynamic_tool_list() -> list[dict[str, Any]]:
    return list_dynamic_tools(get_dynamic_paths())


@tool(
    name="dynamic.tool_test",
    description=(
        "Run a test for a dynamic tool. The test is a self-contained "
        "pytest file that defines a function called `test_<tool_name>`. "
        "Runs in a subprocess with a 30s timeout. Returns {ok, returncode, "
        "stdout, stderr, duration_ms}."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Tool name being tested."},
            "test_body": {
                "type": "string",
                "description": (
                    "Python source of a pytest file. Must define "
                    "`def test_<name>(): ...` and assert things."
                ),
            },
        },
        "required": ["name", "test_body"],
    },
)
async def dynamic_tool_test(name: str, test_body: str) -> dict[str, Any]:
    return run_tool_test(get_dynamic_paths(), name=name, test_body=test_body)


@tool(
    name="dynamic.skill_create",
    description=(
        "Create a new skill (markdown + YAML frontmatter) that will "
        "auto-load in future sessions when the task matches. Use this "
        "after you've figured out a non-obvious procedure that should "
        "be remembered: a deploy dance, a quirky API, a known pitfall. "
        "The body is the markdown content of the skill, without the "
        "YAML frontmatter (added automatically). Side-effect: requires confirm."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name in kebab-case (lowercase, hyphens ok).",
            },
            "description": {
                "type": "string",
                "description": "One-line summary of when the skill applies.",
            },
            "triggers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Phrases that, if found in the task, will auto-load the skill.",
            },
            "body": {
                "type": "string",
                "description": "Markdown body of the skill (procedures, gotchas, examples).",
            },
        },
        "required": ["name", "description", "triggers", "body"],
    },
    side_effect=True,
    requires_confirm=True,
)
async def dynamic_skill_create(
    name: str, description: str, triggers: list[str], body: str
) -> dict[str, Any]:
    from odc.code.auto_extend import is_allowed
    if not is_allowed("dynamic.skill_create"):
        return {
            "ok": False,
            "error": "auto-extension disabled (skill_create off)",
            "name": name,
        }
    paths = get_dynamic_paths()
    skill_path = write_skill(
        paths, name=name, description=description, triggers=triggers, body=body
    )
    return {"ok": True, "name": name, "path": str(skill_path)}


# ---- registry references --------------------------------------------------


# Tools created dynamically need to be registered with the live tool
# registries. The agent loop calls `set_tool_registry()` once at
# startup; the dynamic tools walk this list to register new tools.
_registry_refs: list = []


def set_tool_registry(registry) -> None:
    """Register a tool registry so dynamic tools land in it."""
    if registry not in _registry_refs:
        _registry_refs.append(registry)


def all_dynamic_tools():
    """Return every dynamic.* tool, in registration order."""
    return [
        dynamic_tool_create,
        dynamic_tool_load,
        dynamic_tool_list,
        dynamic_tool_test,
        dynamic_skill_create,
    ]
