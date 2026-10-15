"""ODC v4 — Open Digital Companion, a self-improving LLM agent.

Cognitive architecture, persistent memory, native tools, dynamic tool
creation, AST+LLM self-heal, browser automation, MCP memory substrate,
OSINT sensors, and a 3D web interface.
"""
from odc.version import __version__

# Re-export Agent lazily to avoid circular imports
def __getattr__(name):
    if name == "Agent":
        from odc.agent import Agent
        return Agent
    if name == "AgentRun":
        from odc.agent import AgentRun
        return AgentRun
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["__version__", "Agent", "AgentRun"]
