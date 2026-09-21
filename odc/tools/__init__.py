"""Tool registry: wires up the built-in tools.

The agent imports build_default_registry() and gets a ready-to-use set.
Side-effect tools (shell, write, edit) are flagged for confirm=True.
"""
from __future__ import annotations

from odc.config import Config
from odc.tools.base import ToolRegistry
from odc.tools.file import edit_file, list_dir, read_file, write_file
from odc.tools.memory import memory_recent, memory_save, memory_search
from odc.tools.search import grep
from odc.tools.shell import shell_run
from odc.tools.web import fetch, search


def build_default_registry(config: Config, *, with_memory: bool = True) -> ToolRegistry:
    """Construct a registry with all the default tools.

    The `with_memory` flag lets tests skip the memory tools (which require
    a configured store) when exercising the rest of the system.
    """
    r = ToolRegistry()

    # Read-only / safe-by-default
    r.register(read_file)
    r.register(list_dir)
    r.register(grep)
    r.register(fetch)
    r.register(search)

    # Side-effect tools
    r.register(write_file)
    r.register(edit_file)
    r.register(shell_run)

    # Memory — registered last so a missing store doesn't break load.
    if with_memory:
        r.register(memory_save)
        r.register(memory_search)
        r.register(memory_recent)

    return r
