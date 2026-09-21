"""The Agent: wires LLM + Tools + Skills + Memory into a runnable thing.

Use this from Python. The CLI is just a thin wrapper around Agent.run().
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from odc.config import Config
from odc.llm import Provider, get_provider
from odc.loop import Loop, LoopResult, UserConfirmFn
from odc.memory import MemoryStore
from odc.observability import get_logger, log_event, setup_logging
from odc.skills import (
    builtin_skills,
    coding_skill,
    discover_skills,
    obstacle_breaker_skill,
    skills_matching,
)
from odc.tools import build_default_registry
from odc.tools.base import ToolRegistry
from odc.tools.memory import set_store as set_memory_tool_store

log = get_logger("odc.agent")


@dataclass
class AgentRun:
    task: str
    result: LoopResult
    skills_used: list[str] = field(default_factory=list)
    skills_saved: list[str] = field(default_factory=list)
    saved_memory: list[str] = field(default_factory=list)
    # Real LLM calls captured from the provider. Each entry is
    # (messages, tools) — what was actually sent on the wire.
    # Used by e2e tests, observability, and prompt-compression research.
    provider_calls: list[tuple[list, list | None]] = field(default_factory=list)


class Agent:
    """The agent. Construct once, run many tasks.

    >>> cfg = Config()
    >>> agent = Agent(cfg)
    >>> run = await agent.run("summarize what's in /tmp")
    >>> print(run.result.report[:200])
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        provider: Provider | None = None,
        tools: ToolRegistry | None = None,
        skills_dir: Path | None = None,
        auto_approve: bool = False,
        interactive: bool = True,
        with_memory: bool = True,
        extra_skills: list | None = None,
    ) -> None:
        self.config = config or Config()
        self.config.ensure_dirs()
        setup_logging(self.config.log_level, self.config.data_dir / "logs")

        # LLM
        self.provider = provider or get_provider(self.config)
        # Make the provider available to cognitive tools that need an LLM
        # but don't get it passed explicitly (council, revise, analogy,
        # hypothesize_v2, decompose). Set module-level so they can find
        # it without threading through every call.
        from odc.cognitive import tools as _cogtools
        _cogtools.LLM_PROVIDER = self.provider

        # Memory (real, persistent).
        self.memory = (
            MemoryStore(self.config.data_dir / "memory" / "odc.db") if with_memory else None
        )
        if self.memory is not None:
            set_memory_tool_store(self.memory)
            log_event(log, 20, "memory_ready", path=str(self.memory.db_path), count=self.memory.count())

        # Tools
        if tools is None:
            self.tools = build_default_registry(self.config, with_memory=with_memory)
            # Always-on native code tools (read/grep/edit/todo/etc).
            # Same Python process, no external deps.
            from odc.code import all_code_tools as _code_tools
            from odc.code import all_dynamic_tools as _dyn_tools
            from odc.code import DynamicPaths, set_dynamic_paths, set_tool_registry
            from odc.cognitive import all_cognitive_tools as _cog_tools
            from odc.cognitive.tools import set_paths as _set_cog_paths
            from odc.code.analysis import all_analysis_tools as _analysis_tools

            from odc.osint.tools import register_all as _register_osint
            from odc.osint.tools_keyed import register_all as _register_osint_keyed
            from odc.mcp.tools import register_all as _register_mcp
            from odc.refinement.tools import register_all as _register_refinement
            for t in _code_tools() + _dyn_tools() + _cog_tools() + _analysis_tools():
                try:
                    self.tools.register(t)
                except ValueError:
                    # already registered (e.g. in tests)
                    pass
            # OSINT — real-time eyes on the planet (flights, earthquakes,
            # CVE, bitcoin, crypto prices, space weather, weather, wikipedia).
            for t in _register_osint(self.tools):
                pass  # already registered via register_all
            # OSINT (keyed) — sanctions, satellites, fires, EONET, news.
            # These degrade gracefully without keys; agent learns which work.
            for t in _register_osint_keyed(self.tools):
                pass
            # MCP memory substrate — asuramaya/Osiris 5-step ritual.
            # mount → status → graph_search → record_decision → settle.
            for t in _register_mcp(self.tools, self.config.data_dir):
                pass
            # Level 9: Self-refinement engine (field-grounded, constitutional).
            # 5 tools: record_outcome, evaluate, apply_proposal, rollback, journal.
            for t in _register_refinement(self.tools, self.config.data_dir):
                pass
            # Wire the dynamic prompt builder: tool.discover lets the
            # LLM request additional tools beyond the current subset.
            from odc.prompt.builder import build_tool_discover_tool_description
            from odc.tools.base import Tool
            self.tools.register(_make_discover_tool(self.tools))
            self._dynamic_prompt_enabled = True
            # Wire the dynamic tools up: where to write, which registry
            # to register into.
            dyn_paths = DynamicPaths.for_data_dir(self.config.data_dir)
            set_dynamic_paths(dyn_paths)
            set_tool_registry(self.tools)
            # Cognitive tools need to know where to store thoughts / skills.
            _set_cog_paths(self.config.data_dir / "cognitive")
        else:
            self.tools = tools

        # Skills: builtin + coding + obstacle_breaker + intuition + any on disk + extras.
        from odc.skills.intuition import intuition_skill as _intuition
        self.skills = builtin_skills() + [coding_skill(), obstacle_breaker_skill(), _intuition()]

        # Identity: load and inject into the system prompt.
        from odc.identity import load_identity
        from odc.skills.loader import Skill
        self.identity = load_identity(self.config.data_dir)
        identity_skill = Skill(
            name="identity",
            description="Persistent identity for the agent (name, voice, mannerisms).",
            body=self.identity.to_system_block(),
            triggers=["identity", "who are you", "your name"],
            path=Path(__file__),
            meta={"builtin": True, "category": "identity"},
        )
        # Add identity as a skill, deduplicating by name
        if identity_skill.name not in {s.name for s in self.skills}:
            self.skills.insert(0, identity_skill)
        # Auto-load skills from two places: the configured skills_dir AND
        # the dynamic/skills/ directory that cognitive.self_assess writes
        # to. Both are user-data and should be reloaded on every run.
        sd = skills_dir or (self.config.data_dir / "skills")
        for sdir in [sd, self.config.data_dir / "dynamic" / "skills"]:
            if sdir.exists() and sdir != sd:
                pass
            if sdir.exists():
                for s in discover_skills(sdir):
                    if s.name not in {x.name for x in self.skills}:
                        self.skills.append(s)
        for s in extra_skills or []:
            if s.name not in {x.name for x in self.skills}:
                self.skills.append(s)
        log_event(log, 20, "skills_loaded", count=len(self.skills), names=[s.name for s in self.skills])

        # Confirm strategy.
        self.confirm = UserConfirmFn(auto_approve=auto_approve, interactive=interactive)

    # ------------------------------------------------------------------ public

    async def run(self, task: str, *, thread_id: str | None = None) -> AgentRun:
        """Run a single task. Returns an AgentRun with the report + metadata.

        thread_id: optional. If a checkpoint exists for this id, the
        loop resumes from the last saved turn instead of starting over.
        """
        active = skills_matching(self.skills, task) or self.skills
        log_event(
            log,
            20,
            "agent_run",
            task=task[:200],
            active_skills=[s.name for s in active],
            thread_id=thread_id,
        )

        loop = Loop(
            config=self.config,
            provider=self.provider,
            tools=self.tools,
            skills=active,
            confirm=self.confirm,
        )
        self.loop = loop  # for tests to inspect provider.calls after run
        result = await loop.run(task, thread_id=thread_id)

        # Persist a 'task' memory entry so future sessions can recall it.
        saved_memory: list[str] = []
        if self.memory is not None and result.report:
            eid = self.memory.save(
                text=f"Task: {task}\n\nOutcome:\n{result.report[:1500]}",
                category="task",
                source="agent",
            )
            saved_memory.append(eid)

        return AgentRun(
            task=task,
            result=result,
            skills_used=[s.name for s in active],
            saved_memory=saved_memory,
            provider_calls=getattr(self.loop.provider, "calls", []),
        )

    # ----- introspection helpers -----

    def tool_names(self) -> list[str]:
        return self.tools.names()

    def get_tool_subset(self, task: str, recent: list[str] | None = None) -> list[str]:
        """Phase 2 of the dynamic prompt: return the BM25-matched
        subset of tools for this task. Used to limit what the LLM
        can see in Layer 2 of the system prompt."""
        from odc.prompt.bm25 import BM25, build_corpus_from_tools
        from odc.prompt.builder import build_layer_2
        all_tools = []
        for n in self.tools.names():
            t = self.tools.get(n)
            if t is not None:
                all_tools.append(t)
        _, selected = build_layer_2(
            task=task, all_tools=all_tools, recent_tool_calls=recent or [],
        )
        return selected

    def skill_names(self) -> list[str]:
        return [s.name for s in self.skills]


def _make_discover_tool(registry):
    """Factory for the tool.discover tool. When called by the LLM, it
    expands the active tool subset by BM25-matching the query against
    the full catalog. The matched tools are recorded as 'recent' so
    the next pre_task_brief includes them in Layer 2."""
    from odc.prompt.bm25 import BM25, build_corpus_from_tools

    async def _discover(query: str, max_tools: int = 3) -> dict:
        all_tools = []
        names_list: list[str] = []
        for n in registry.names():
            t = registry.get(n)
            if t is None:
                continue
            all_tools.append(t)
            names_list.append(n)
        if not all_tools:
            return {"ok": False, "error": "no tools in registry", "added": []}
        corpus, names = build_corpus_from_tools(all_tools)
        ranker = BM25(corpus)
        ranked = ranker.rank(query, top_k=max_tools, min_score=0.0)
        added = [names[i] for i in ranked]
        # Store on the registry for the next pre_task_brief
        registry._last_discover = {"query": query, "tools": added}
        return {
            "ok": True,
            "query": query,
            "added": added,
            "hint": (
                f"Added {len(added)} tools to the active subset for this "
                f"and subsequent turns: {added}. Use them as if they had "
                f"been there from the start."
            ),
        }

    from odc.tools.base import Tool, tool

    @tool(
        name="tool.discover",
        description=(
            "Expand the current tool subset. Pass a keyword query and "
            "the most relevant tools (up to 5) will be added to the "
            "active set for this turn and the next. Use this when the "
            "task needs a tool that isn't currently in your subset "
            "(see [TOOL SUBSET] in your system prompt)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword query, e.g. 'git history' or 'json parse' or 'sha256'.",
                },
                "max_tools": {
                    "type": "integer",
                    "description": "Maximum tools to add. Default 3, max 5.",
                },
            },
            "required": ["query"],
        },
    )
    async def tool_discover(query: str, max_tools: int = 3) -> dict:
        return await _discover(query=query, max_tools=max_tools)
    return tool_discover
