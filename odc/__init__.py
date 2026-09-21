"""ODC v4 — honest LLM agent.

Top-level package surface. The real work lives in submodules; this
file just re-exports the things a typical user calls.
"""
from odc.agent import Agent, AgentRun
from odc.config import Config
from odc.loop import LoopResult
from odc.memory import MemoryStore
from odc.skills import Skill
from odc.tools import ToolRegistry

__version__ = "4.0.0"
__all__ = [
    "Agent",
    "AgentRun",
    "Config",
    "LoopResult",
    "MemoryStore",
    "Skill",
    "ToolRegistry",
    "__version__",
]
