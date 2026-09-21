"""Tests for file tools (write, read, edit) — the core side-effect path."""
from __future__ import annotations

from pathlib import Path

import pytest

from odc.tools.file import edit_file, list_dir, read_file, write_file


@pytest.mark.asyncio
async def test_read_existing(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("hello world")
    res = await read_file(path=str(p))
    assert res.success is True
    assert res.output == "hello world"


@pytest.mark.asyncio
async def test_read_missing(tmp_path: Path):
    res = await read_file(path=str(tmp_path / "missing.txt"))
    assert res.success is False
    assert "No such file" in res.error


@pytest.mark.asyncio
async def test_write_requires_confirm(tmp_path: Path):
    p = tmp_path / "f.txt"
    res = await write_file(path=str(p), content="data")
    assert res.success is False
    assert "requires explicit confirmation" in res.error


@pytest.mark.asyncio
async def test_write_creates_parents(tmp_path: Path):
    p = tmp_path / "deep" / "nested" / "f.txt"
    res = await write_file(path=str(p), content="ok", confirm=True)
    assert res.success is True
    assert p.read_text() == "ok"


@pytest.mark.asyncio
async def test_edit_unique_replacement(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("alpha beta gamma")
    res = await edit_file(
        path=str(p), old_text="beta", new_text="BETA", confirm=True
    )
    assert res.success is True
    assert p.read_text() == "alpha BETA gamma"


@pytest.mark.asyncio
async def test_edit_ambiguous_replacement_fails(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("foo foo foo")
    res = await edit_file(path=str(p), old_text="foo", new_text="bar", confirm=True)
    assert res.success is False
    assert "appears" in res.error


@pytest.mark.asyncio
async def test_edit_missing_replacement_fails(tmp_path: Path):
    p = tmp_path / "f.txt"
    p.write_text("hello")
    res = await edit_file(path=str(p), old_text="bye", new_text="x", confirm=True)
    assert res.success is False
    assert "not found" in res.error


@pytest.mark.asyncio
async def test_list_dir_basic(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b").mkdir()
    res = await list_dir(path=str(tmp_path))
    assert res.success is True
    names = res.output
    assert any("a.txt" in n for n in names)
    assert any("b" in n for n in names)


@pytest.mark.asyncio
async def test_list_dir_glob(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    res = await list_dir(path=str(tmp_path), pattern="*.py")
    assert res.success is True
    assert any("a.py" in n for n in res.output)
    assert not any("b.txt" in n for n in res.output)
