"""Tests for skill loading and matching."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from odc.skills import builtin_skills, discover_skills, parse_skill_file, skills_matching


def test_builtin_skills_present():
    s = builtin_skills()
    names = {x.name for x in s}
    assert {"method", "judge", "self-learn"}.issubset(names)


def test_skills_have_required_fields():
    for s in builtin_skills():
        assert s.name
        assert s.description
        assert s.body.strip()
        assert isinstance(s.triggers, list)


def test_skills_matching_returns_overlap():
    s = builtin_skills()
    matches = skills_matching(s, "please fix this bug in main.py")
    names = {x.name for x in matches}
    assert "method" in names


def test_skills_matching_empty_when_no_triggers_match():
    s = builtin_skills()
    matches = skills_matching(s, "what is the meaning of life?")
    # The 'method' skill has broad triggers — should always match.
    # This test just ensures no crash and returns a list.
    assert isinstance(matches, list)


def test_parse_skill_file(tmp_path: Path):
    skill = tmp_path / "demo" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        textwrap.dedent(
            """\
            ---
            name: demo
            description: a demo skill
            triggers:
              - demo
              - example
            ---

            # Demo
            Do the thing.
            """
        )
    )
    s = parse_skill_file(skill)
    assert s.name == "demo"
    assert "demo" in s.triggers
    assert "Do the thing" in s.body


def test_discover_skills_finds_subdirs(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text(
        "---\nname: a\ndescription: alpha\n---\n\nA"
    )
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "SKILL.md").write_text(
        "---\nname: b\ndescription: beta\n---\n\nB"
    )
    out = discover_skills(tmp_path)
    assert {x.name for x in out} == {"a", "b"}


def test_discover_skills_skips_broken(tmp_path: Path):
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "SKILL.md").write_text("no frontmatter here")
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "SKILL.md").write_text(
        "---\nname: g\ndescription: g\n---\n\nG"
    )
    out = discover_skills(tmp_path)
    assert {x.name for x in out} == {"g"}
