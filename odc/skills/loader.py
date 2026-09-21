"""Skill loading.

A skill is a markdown file with YAML frontmatter describing its
trigger, plus a body the LLM reads as instructions. Skills don't run
code in the agent — they shape prompts and (optionally) inject tools.

Layout:
    skills/
      method/SKILL.md      # fable-method
      judge/SKILL.md       # fable-judge
      self-learn/SKILL.md  # self-learning

The agent reads the relevant skills at task start, prepends their
bodies to the system prompt, and the LLM follows them literally.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from odc.observability import get_logger

log = get_logger("odc.skills")

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    triggers: list[str]
    path: Path
    meta: dict[str, Any]

    def matches(self, task: str) -> bool:
        """Cheap heuristic: does any trigger phrase appear in the task?"""
        if not self.triggers:
            return False
        lo = task.lower()
        return any(t.lower() in lo for t in self.triggers)


def parse_skill_file(path: Path) -> Skill:
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError(f"Skill {path} has no YAML frontmatter (---...---)")
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2).strip()
    name = fm.get("name") or path.parent.name
    description = fm.get("description", "").strip()
    triggers = list(fm.get("triggers") or [])
    return Skill(
        name=name,
        description=description,
        body=body,
        triggers=triggers,
        path=path,
        meta=fm,
    )


def discover_skills(skills_dir: Path) -> list[Skill]:
    """Find all SKILL.md under `skills_dir`."""
    if not skills_dir.exists():
        return []
    out: list[Skill] = []
    for skill_md in sorted(skills_dir.rglob("SKILL.md")):
        try:
            out.append(parse_skill_file(skill_md))
        except Exception as e:  # noqa: BLE001
            log.warning("failed to load skill %s: %s", skill_md, e)
    return out


def skills_matching(skills: list[Skill], task: str) -> list[Skill]:
    """Return the skills whose triggers match the task. Order preserved."""
    return [s for s in skills if s.matches(task)]


def format_skill_block(skills: list[Skill]) -> str:
    """Format the selected skills into a system-prompt block."""
    if not skills:
        return ""
    parts = ["# ACTIVE SKILLS\n"]
    for s in skills:
        parts.append(f"\n## Skill: {s.name}\n")
        parts.append(f"_Triggered because: description matches the task._\n\n")
        parts.append(s.body)
        parts.append("\n")
    return "".join(parts)
