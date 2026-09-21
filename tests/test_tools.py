"""Tests for the tool base + registry.

Covers: tool contract, error handling, side-effect confirmation,
registry round-trip, and the @tool decorator.
"""
from __future__ import annotations

import pytest

from odc.tools.base import Tool, ToolRegistry, ToolResult, tool


# --- @tool decorator ---------------------------------------------------------

def test_decorator_makes_callable_tool():
    @tool(
        name="add",
        description="add two numbers",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )
    def add(a: int, b: int) -> int:
        return a + b

    assert isinstance(add, Tool)
    assert add.name == "add"
    spec = add.spec()
    assert spec.name == "add"
    assert "a" in spec.parameters["properties"]


@pytest.mark.asyncio
async def test_decorated_tool_runs_async_correctly():
    @tool(name="double", description="x2", parameters={"type": "object", "properties": {}})
    def double(x: int) -> int:
        return x * 2

    res = await double(x=4)
    assert res.success is True
    assert res.output == 8


@pytest.mark.asyncio
async def test_tool_returns_error_on_exception():
    @tool(name="boom", description="raises", parameters={"type": "object", "properties": {}})
    def boom() -> int:
        raise RuntimeError("kaboom")

    res = await boom()
    assert res.success is False
    assert "kaboom" in res.error


@pytest.mark.asyncio
async def test_side_effect_tool_requires_confirm():
    @tool(
        name="destroy",
        description="oh no",
        parameters={"type": "object", "properties": {}},
        side_effect=True,
        requires_confirm=True,
    )
    def destroy() -> str:
        return "destroyed"

    res = await destroy()
    assert res.success is False
    assert "requires explicit confirmation" in res.error

    res = await destroy(confirm=True)
    assert res.success is True
    assert res.output == "destroyed"


# --- Registry --------------------------------------------------------------

def test_registry_register_and_get():
    r = ToolRegistry()
    t = tool(name="echo", description="echo", parameters={"type": "object", "properties": {}})(
        lambda x: x
    )
    r.register(t)
    assert "echo" in r.names()
    assert r.get("echo") is t


def test_registry_duplicate_name_raises():
    r = ToolRegistry()
    t1 = tool(name="dup", description="d", parameters={"type": "object", "properties": {}})(
        lambda: 1
    )
    t2 = tool(name="dup", description="d", parameters={"type": "object", "properties": {}})(
        lambda: 2
    )
    r.register(t1)
    with pytest.raises(ValueError):
        r.register(t2)


@pytest.mark.asyncio
async def test_registry_run_unknown():
    r = ToolRegistry()
    res = await r.run("nope", x=1)
    assert res.success is False
    assert "Unknown tool" in res.error
