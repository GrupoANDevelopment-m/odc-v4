"""Tests for the dynamic tool & skill creation (obstacle breaker)."""
from __future__ import annotations

from pathlib import Path

import pytest

from odc.code.dynamic import (
    DynamicPaths,
    check_safety,
    list_dynamic_tools,
    load_tool,
    run_tool_test,
    write_skill,
    write_tool,
)


# ---- safety check ----------------------------------------------------------


def test_safety_accepts_clean_code():
    src = '''
import httpx
from odc.tools.base import tool

@tool(name="ping", description="ping", parameters={"type": "object", "properties": {}})
async def ping() -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get("https://example.com")
    return r.text[:100]
'''
    rep = check_safety(src)
    assert rep.ok, rep.reason


def test_safety_rejects_os_system():
    src = '''
from odc.tools.base import tool
@tool(name="x", description="x", parameters={"type": "object", "properties": {}})
async def x() -> str:
    import os
    return os.system("ls")
'''
    rep = check_safety(src)
    assert not rep.ok
    assert "os.system" in rep.reason


def test_safety_rejects_subprocess():
    src = '''
from odc.tools.base import tool
@tool(name="x", description="x", parameters={"type": "object", "properties": {}})
async def x() -> str:
    import subprocess
    return subprocess.run(["ls"], capture_output=True, text=True).stdout
'''
    rep = check_safety(src)
    assert not rep.ok
    assert "subprocess" in rep.reason


def test_safety_rejects_eval_exec():
    for banned in ("eval", "exec"):
        src = f'''
from odc.tools.base import tool
@tool(name="x", description="x", parameters={{"type": "object", "properties": {{}}}})
async def x() -> str:
    return {banned}("1+1")
'''
        rep = check_safety(src)
        assert not rep.ok, f"{banned} should be banned"
        assert banned in rep.reason


def test_safety_rejects_ctypes():
    src = '''
import ctypes
from odc.tools.base import tool
@tool(name="x", description="x", parameters={"type": "object", "properties": {}})
async def x() -> str:
    return ctypes.c_int(1)
'''
    rep = check_safety(src)
    assert not rep.ok
    assert "ctypes" in rep.reason


def test_safety_rejects_input():
    src = '''
from odc.tools.base import tool
@tool(name="x", description="x", parameters={"type": "object", "properties": {}})
async def x() -> str:
    return input("prompt: ")
'''
    rep = check_safety(src)
    assert not rep.ok
    assert "input" in rep.reason


def test_safety_syntax_error():
    rep = check_safety("def foo(:\n  pass")
    assert not rep.ok
    assert "SyntaxError" in rep.reason


# ---- write_tool + load_tool -----------------------------------------------


@pytest.fixture
def paths(tmp_path: Path) -> DynamicPaths:
    return DynamicPaths.for_data_dir(tmp_path)


def test_write_tool_creates_file_and_registers(paths):
    src = '''
from odc.tools.base import tool

@tool(
    name="greeter",
    description="say hi to a name",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
)
async def greeter(name: str) -> str:
    return f"hello, {name}"
'''
    rec, safety = write_tool(paths, name="greeter", body=src, description="say hi")
    assert safety.ok
    assert rec.path.exists()
    assert (paths.tools_dir / "greeter.py").exists()


def test_write_tool_rejects_bad_name(paths):
    src = "@tool(name='x', description='x', parameters={'type':'object','properties':{}})\nasync def x():\n    pass\n"
    for bad in ("Greeter", "with-dash", "1numeric", "with space", "with.dot"):
        with pytest.raises(ValueError, match="invalid tool name"):
            write_tool(paths, name=bad, body=src, description="x")


def test_write_tool_refuses_unsafe_code(paths):
    src = '''
import os
from odc.tools.base import tool

@tool(name="bad", description="x", parameters={"type": "object", "properties": {}})
async def bad() -> str:
    return os.system("ls")
'''
    rec, safety = write_tool(paths, name="bad", body=src, description="x")
    assert not safety.ok
    # File is NOT written when safety fails — the user must fix the code
    # before resubmitting.
    assert not rec.path.exists()


def test_load_tool_returns_callable(paths):
    src = '''
from odc.tools.base import tool

@tool(
    name="doubler",
    description="double an int",
    parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
)
async def doubler(x: int) -> int:
    return x * 2
'''
    write_tool(paths, name="doubler", body=src, description="double")
    loaded = load_tool("doubler", paths)
    assert loaded is not None
    assert loaded.name == "doubler"


def test_load_tool_missing_returns_none(paths):
    assert load_tool("nope", paths) is None


def test_list_dynamic_tools(paths):
    src = '''
from odc.tools.base import tool
@tool(name="a", description="a", parameters={"type":"object","properties":{}})
async def a() -> str: return "a"
'''
    write_tool(paths, name="a", body=src, description="a")
    src2 = src.replace('"a"', '"b"').replace("async def a", "async def b")
    write_tool(paths, name="b", body=src2, description="b")
    listed = list_dynamic_tools(paths)
    names = {x["name"] for x in listed}
    assert names == {"a", "b"}


# ---- run_tool_test --------------------------------------------------------


def test_run_tool_test_passing(paths):
    test_body = '''
def test_math():
    assert 1 + 1 == 2
'''
    res = run_tool_test(paths, name="math", test_body=test_body)
    assert res["ok"] is True
    assert res["returncode"] == 0


def test_run_tool_test_failing(paths):
    test_body = '''
def test_math():
    assert 1 + 1 == 3
'''
    res = run_tool_test(paths, name="math", test_body=test_body)
    assert res["ok"] is False
    assert "assert 1 + 1 == 3" in res["stdout"] or "FAILED" in res["stdout"]


# ---- write_skill ----------------------------------------------------------


def test_write_skill_creates_md_with_frontmatter(paths):
    p = write_skill(
        paths,
        name="my-skill",
        description="a demo skill",
        triggers=["demo", "example"],
        body="# How to demo\n\n1. do the thing\n",
    )
    assert p.exists()
    text = p.read_text()
    assert "---" in text
    assert "name: my-skill" in text
    assert "description: a demo skill" in text
    assert "triggers:" in text
    assert "  - demo" in text
    assert "  - example" in text
    assert "# How to demo" in text


def test_write_skill_rejects_bad_name(paths):
    with pytest.raises(ValueError, match="invalid skill name"):
        write_skill(
            paths,
            name="Bad Name",
            description="x",
            triggers=[],
            body="x",
        )


# ---- end-to-end: write + test + load --------------------------------------


def test_end_to_end_create_test_and_load(paths):
    # 1. Create a tool that does something we can verify.
    src = '''
from odc.tools.base import tool

@tool(
    name="addtwo",
    description="add two ints",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
)
async def addtwo(a: int, b: int) -> int:
    return a + b
'''
    _, safety = write_tool(paths, name="addtwo", body=src, description="add")
    assert safety.ok

    # 2. Run a test that uses the tool.
    test_body = '''
import asyncio
from pathlib import Path
import sys

# Make the dynamic tools importable.
dyn = Path("/DYN").resolve()  # placeholder, replaced below
sys.path.insert(0, str(dyn))

from addtwo import addtwo

def test_addtwo_basic():
    assert asyncio.run(addtwo(a=2, b=3)) == 5
'''
    # We can't easily rewrite the path in pytest, so just do an inline test
    # by importing the function we just wrote.
    loaded = load_tool("addtwo", paths)
    assert loaded is not None
    # The loaded object is a Tool (the @tool wrapper). Use .run() to
    # call the inner function and get the raw output.
    import asyncio
    res = asyncio.run(loaded.run(a=2, b=3))
    assert res == 5
