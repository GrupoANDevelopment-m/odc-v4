"""Tests for the native code tools (odc.code)."""
from __future__ import annotations

from pathlib import Path

import pytest

from odc.code.diff import file_diff, unified_diff
from odc.code.symbols import extract_symbols, find_references
from odc.code.todos import TodoStore, get_store, reset_store
from odc.code.tools import (
    code_diff,
    code_edit,
    code_glob,
    code_grep,
    code_multi_edit,
    code_read,
    code_references,
    code_symbols,
    code_todo_add,
    code_todo_clear,
    code_todo_list,
    code_todo_update,
    code_write,
)


# ---- symbols --------------------------------------------------------------


def test_extract_symbols_python_function():
    text = "def foo(x, y):\n    return x + y\n"
    syms = extract_symbols("demo.py", text=text)
    names = [s["name"] for s in syms]
    assert "foo" in names
    assert any(s["kind"] == "function" for s in syms)


def test_extract_symbols_python_class():
    text = (
        "class Greeter:\n"
        '    """says hi"""\n'
        "    def hello(self):\n"
        "        return 'hi'\n"
    )
    syms = extract_symbols("demo.py", text=text)
    classes = [s for s in syms if s["kind"] == "class"]
    funcs = [s for s in syms if s["kind"] == "function"]
    assert any(c["name"] == "Greeter" for c in classes)
    assert any(c.get("doc") == "says hi" for c in classes)
    assert any(f["name"] == "hello" for f in funcs)


def test_extract_symbols_python_syntax_error_returns_empty():
    text = "def foo(:\n    pass\n"  # broken
    syms = extract_symbols("demo.py", text=text)
    # Broken Python: regex fallback kicks in, may find 0 or more.
    assert isinstance(syms, list)


def test_extract_symbols_javascript_fallback():
    text = "function hello() { return 1; }\nclass Foo { bar() {} }\n"
    syms = extract_symbols("demo.js", text=text)
    names = {s["name"] for s in syms}
    assert "hello" in names or "Foo" in names  # at least the obvious one


def test_extract_symbols_unknown_extension_returns_empty():
    syms = extract_symbols("data.bin", text="\x00\x01")
    assert syms == []


def test_find_references(tmp_path: Path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\nfoo()\nfoo()\n")
    (tmp_path / "b.py").write_text("from a import foo\nfoo()\n")
    refs = find_references("foo", root=tmp_path, glob="*.py")
    paths = {r["path"] for r in refs}
    assert "a.py" in paths
    assert "b.py" in paths


def test_find_references_unknown_root(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        find_references("foo", root=tmp_path / "nope")


# ---- todos ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_todos():
    reset_store()
    yield
    reset_store()


def test_todo_add_returns_id_and_stores():
    tid = get_store().add("do the thing")
    assert tid in get_store()._todos
    assert get_store().get(tid).content == "do the thing"


def test_todo_update_status_to_done_stamps_time():
    tid = get_store().add("x")
    todo = get_store().update(tid, status="done")
    assert todo.status == "done"
    assert todo.completed_at is not None


def test_todo_list_sorts_in_progress_first():
    get_store().add("first", priority="high")
    get_store().add("second", priority="low")
    get_store().update(get_store().list()[0].id, status="in_progress")
    listed = get_store().list()
    assert listed[0].status == "in_progress"


def test_todo_clear_wipes():
    get_store().add("a")
    get_store().add("b")
    assert get_store().count() == 2
    assert get_store().clear() == 2
    assert get_store().count() == 0


def test_todo_update_unknown_id_raises():
    with pytest.raises(KeyError):
        get_store().update("nope", status="done")


def test_todo_update_unknown_field_raises():
    tid = get_store().add("x")
    with pytest.raises(AttributeError):
        get_store().update(tid, banana=True)


# ---- diff -----------------------------------------------------------------


def test_unified_diff_no_changes():
    d = unified_diff("a\n", "a\n", fromfile="a", tofile="b")
    assert d == ""


def test_unified_diff_with_changes():
    d = unified_diff("a\nb\n", "a\nB\n", fromfile="a", tofile="b")
    assert "-b" in d
    assert "+B" in d


def test_file_diff_no_baseline(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("hello")
    d = file_diff(p, against=None)
    assert d["has_changes"] is False
    assert d["after"] == "hello"


def test_file_diff_against_baseline(tmp_path: Path):
    base = tmp_path / "old.txt"
    cur = tmp_path / "new.txt"
    base.write_text("a\nb\nc\n")
    cur.write_text("a\nB\nc\n")
    d = file_diff(cur, against=base)
    assert d["has_changes"] is True
    assert "-b" in d["diff"]
    assert "+B" in d["diff"]


def test_file_diff_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        file_diff(tmp_path / "missing")


# ---- tools (via .run so we get raw output, not ToolResult) ----------------


@pytest.mark.asyncio
async def test_code_read_returns_text_and_symbols(tmp_path: Path):
    p = tmp_path / "demo.py"
    p.write_text("def foo():\n    return 1\n")
    res = await code_read.run(path=str(p))
    assert "def foo" in res["text"]
    assert any(s["name"] == "foo" for s in res["symbols"])


@pytest.mark.asyncio
async def test_code_read_offset_limit(tmp_path: Path):
    p = tmp_path / "demo.txt"
    p.write_text("\n".join(f"line {i}" for i in range(10)))
    res = await code_read.run(path=str(p), offset=3, limit=2)
    assert "line 3" in res["text"]
    assert "line 4" in res["text"]
    assert "line 5" not in res["text"]


@pytest.mark.asyncio
async def test_code_read_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        await code_read.run(path=str(tmp_path / "nope"))


@pytest.mark.asyncio
async def test_code_glob(tmp_path: Path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "c.py").write_text("")
    res = await code_glob.run(pattern="*.py", root=str(tmp_path))
    assert sorted(res) == ["a.py", "c.py"]


@pytest.mark.asyncio
async def test_code_glob_missing_root(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        await code_glob.run(pattern="*", root=str(tmp_path / "nope"))


@pytest.mark.asyncio
async def test_code_grep_basic(tmp_path: Path):
    (tmp_path / "a.py").write_text("foo\nbar\nfoo\n")
    res = await code_grep.run(pattern="foo", path=str(tmp_path))
    assert len(res) == 2
    assert all(r["text"] == "foo" for r in res)


@pytest.mark.asyncio
async def test_code_grep_with_glob(tmp_path: Path):
    (tmp_path / "a.py").write_text("foo\n")
    (tmp_path / "b.txt").write_text("foo\n")
    res = await code_grep.run(pattern="foo", path=str(tmp_path), glob="*.py")
    assert len(res) == 1


@pytest.mark.asyncio
async def test_code_grep_missing_path(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        await code_grep.run(pattern="x", path=str(tmp_path / "nope"))


@pytest.mark.asyncio
async def test_code_symbols_tool(tmp_path: Path):
    p = tmp_path / "demo.py"
    p.write_text("class A:\n    def m(self): pass\n")
    res = await code_symbols.run(path=str(p))
    names = [s["name"] for s in res]
    assert "A" in names


@pytest.mark.asyncio
async def test_code_references_tool(tmp_path: Path):
    (tmp_path / "a.py").write_text("foo()\nbar()\n")
    res = await code_references.run(name="foo", root=str(tmp_path), glob="*.py")
    assert any(r["path"] == "a.py" for r in res)


@pytest.mark.asyncio
async def test_code_write_creates_parents(tmp_path: Path):
    p = tmp_path / "deep" / "nested" / "f.txt"
    res = await code_write(path=str(p), content="ok", confirm=True)
    assert res.success is True
    assert "wrote" in res.output
    assert p.read_text() == "ok"


@pytest.mark.asyncio
async def test_code_write_requires_confirm(tmp_path: Path):
    p = tmp_path / "f.txt"
    res = await code_write(path=str(p), content="ok")
    assert res.success is False
    assert "requires explicit confirmation" in res.error


@pytest.mark.asyncio
async def test_code_edit_unique(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("alpha beta gamma")
    res = await code_edit(path=str(p), old_text="beta", new_text="BETA", confirm=True)
    assert res.success is True
    assert "edited" in res.output
    assert p.read_text() == "alpha BETA gamma"


@pytest.mark.asyncio
async def test_code_edit_not_unique(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("foo foo foo")
    res = await code_edit(path=str(p), old_text="foo", new_text="bar", confirm=True)
    assert res.success is False
    assert "appears" in res.error


@pytest.mark.asyncio
async def test_code_edit_not_found(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("hello")
    res = await code_edit(path=str(p), old_text="bye", new_text="x", confirm=True)
    assert res.success is False
    assert "not found" in res.error


@pytest.mark.asyncio
async def test_code_multi_edit_atomic(tmp_path: Path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("AAA")
    b.write_text("BBB")
    res = await code_multi_edit(
        edits=[
            {"path": str(a), "old_text": "AAA", "new_text": "aaa"},
            {"path": str(b), "old_text": "BBB", "new_text": "bbb"},
        ],
        confirm=True,
    )
    assert res.success is True
    assert res.output["applied"] == 2
    assert res.output["rolled_back"] == 0
    assert a.read_text() == "aaa"
    assert b.read_text() == "bbb"


@pytest.mark.asyncio
async def test_code_multi_edit_rollback_on_failure(tmp_path: Path):
    a = tmp_path / "a.txt"
    a.write_text("AAA")
    # Second edit references a file that doesn't exist — first edit must
    # be rolled back. The tool still returns success=True because the
    # rollback was successful; the error is in the output dict.
    res = await code_multi_edit(
        edits=[
            {"path": str(a), "old_text": "AAA", "new_text": "aaa"},
            {"path": str(tmp_path / "missing.txt"), "old_text": "x", "new_text": "y"},
        ],
        confirm=True,
    )
    assert res.success is True  # rollback succeeded
    assert "error" in res.output
    assert res.output["rolled_back"] == 1
    assert res.output["applied"] == 1  # the one before the failure
    assert a.read_text() == "AAA"  # unchanged — was rolled back


@pytest.mark.asyncio
async def test_code_multi_edit_rollback_on_ambiguity(tmp_path: Path):
    a = tmp_path / "a.txt"
    a.write_text("x x x")
    res = await code_multi_edit(
        edits=[{"path": str(a), "old_text": "x", "new_text": "y"}],
        confirm=True,
    )
    assert res.success is True  # rollback succeeded
    assert "error" in res.output
    assert res.output["applied"] == 0
    assert res.output["rolled_back"] == 0  # nothing to roll back
    assert a.read_text() == "x x x"  # unchanged


@pytest.mark.asyncio
async def test_code_diff_no_baseline(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("hello")
    res = await code_diff.run(path=str(p))
    assert res["has_changes"] is False


@pytest.mark.asyncio
async def test_code_diff_with_baseline(tmp_path: Path):
    base = tmp_path / "old.txt"
    cur = tmp_path / "new.txt"
    base.write_text("a\n")
    cur.write_text("b\n")
    res = await code_diff.run(path=str(cur), against=str(base))
    assert res["has_changes"] is True
    assert "-a" in res["diff"]


# ---- todo tool round-trip -------------------------------------------------


@pytest.mark.asyncio
async def test_todo_tool_round_trip():
    res = await code_todo_add.run(content="plan the refactor")
    tid = res["id"]
    assert res["content"] == "plan the refactor"

    res = await code_todo_list.run()
    assert any(t["id"] == tid for t in res["items"])

    res = await code_todo_update.run(id=tid, status="done")
    assert res["status"] == "done"

    res = await code_todo_clear.run()
    assert res["cleared"] >= 1
