"""Native code tools for ODC v4.

Everything in here runs in the ODC v4 process — no subprocess, no
external binary, no HTTP. This is the OpenCode capability surface
re-implemented in pure Python:

  - read files with extracted symbols (Python AST + regex fallback)
  - glob / grep across the project
  - write / edit / multi_edit with atomic rollback
  - todo tracker for multi-step planning
  - diff for verification
  - dynamic tool & skill creation (the "obstacle breaker")

The `all_code_tools()` function returns the full list, ready to be
registered into an ODC v4 tool registry.
"""
from odc.code.diff import file_diff, unified_diff
from odc.code.dynamic import (
    DynamicPaths,
    SafetyReport,
    list_dynamic_tools,
    load_tool,
    run_tool_test,
    write_skill,
    write_tool,
)
from odc.code.dynamic_tools import (
    all_dynamic_tools,
    dynamic_skill_create,
    dynamic_tool_create,
    dynamic_tool_list,
    dynamic_tool_load,
    dynamic_tool_test,
    set_dynamic_paths,
    set_tool_registry,
)
from odc.code.symbols import extract_symbols, find_references
from odc.code.todos import TodoStore, get_store, reset_store
from odc.code.tools import (
    all_code_tools,
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

__all__ = [
    "DynamicPaths",
    "SafetyReport",
    "TodoStore",
    "all_code_tools",
    "all_dynamic_tools",
    "code_diff",
    "code_edit",
    "code_glob",
    "code_grep",
    "code_multi_edit",
    "code_read",
    "code_references",
    "code_symbols",
    "code_todo_add",
    "code_todo_clear",
    "code_todo_list",
    "code_todo_update",
    "code_write",
    "dynamic_skill_create",
    "dynamic_tool_create",
    "dynamic_tool_list",
    "dynamic_tool_load",
    "dynamic_tool_test",
    "extract_symbols",
    "file_diff",
    "find_references",
    "get_store",
    "list_dynamic_tools",
    "load_tool",
    "reset_store",
    "run_tool_test",
    "set_dynamic_paths",
    "set_tool_registry",
    "unified_diff",
    "write_skill",
    "write_tool",
]
