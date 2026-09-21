"""The coding skill.

Loaded into the system prompt whenever the LLM is working on a coding
task with the OpenCode integration. Tells the model:

  - what the code.* tools are and when to use them
  - the difference between ODC's own tools (fs.*, search.grep) and
    the opencode-powered ones (code.*) — short version: code.* has
    LSP and project context, use it for anything that needs to
    understand types/symbols/project state.
  - the recommended pattern: read with code.read, search with
    code.grep/code.glob, edit through code.session_send (which lets
    opencode do its multi-file plan+edit+verify dance), then verify
    with code.diff.

This is a Skill object, not a markdown file, so the agent doesn't
have to discover it at startup — it's part of the built-in set.
"""
from __future__ import annotations

from pathlib import Path

from odc.skills.loader import Skill

CODING = """\
# Coding skill (native code.* tools)

You have a complete set of code tools built into ODC v4. They run in
the same Python process — no subprocess, no external service. Use
them whenever the task is about understanding, modifying, or
verifying code in a real project. Use ODC's other tools (`fs.*`,
`search.grep`, `web.*`) for non-code work.

## What's available

Reading and navigation:
- `code.read(path, offset?, limit?, include_symbols?)` — read a file
  with extracted symbols (functions, classes, methods). Python uses
  the AST; other languages use a regex fallback. Returns the text
  plus a `symbols` list.
- `code.glob(pattern, root?, limit?)` — find files by name.
- `code.grep(pattern, path?, glob?, context?, limit?)` — regex
  search with optional context lines.
- `code.symbols(path)` — list the symbols of a file without the full
  text. Use this when you only need the shape.
- `code.references(name, root?, glob?, limit?)` — find where a symbol
  is used. Lexical search (whole-word), not a real LSP ref-query.

Editing:
- `code.write(path, content)` — write/overwrite. Requires confirm.
- `code.edit(path, old_text, new_text)` — find/replace, must be
  unique. Requires confirm.
- `code.multi_edit(edits[])` — atomic edits across N files. Rolls
  back on the first failure. Requires confirm.

Planning and tracking:
- `code.todo_add(content, priority?)` — add a working-memory todo.
- `code.todo_update(id, status?, ...)` — change status / content.
- `code.todo_list(status?, priority?)` — read the list.
- `code.todo_clear()` — wipe the list (start fresh).

Verification:
- `code.diff(path, against?)` — unified diff of a file vs a saved
  baseline. Use this to verify a coding task actually changed the
  file before claiming done.

## The recommended pattern

1. **Recon.** If the user says "fix the bug" or "add tests for X",
   don't just start editing. `code.grep` + `code.symbols` to map the
   area, then `code.read` for the specific file. Cap at 2 fruitless
   searches before asking the user.

2. **Plan.** For any non-trivial task, drop a few todos with
   `code.todo_add` before editing. Mark the first `in_progress`.
   The plan is the contract; the implementation is the proof.

3. **Edit.** `code.edit` for surgical changes. `code.write` for
   full-file rewrites. `code.multi_edit` for atomic edits across
   files (e.g. rename a function in 4 places). Each `code.edit` /
   `code.write` requires explicit user confirmation — pass
   `confirm=True` from the agent loop, which is the default
   after the user types `y`.

4. **Verify.** After editing, `code.read` (or `code.symbols`) the
   file again to confirm the change is there. For cross-file
   changes, `code.references(name)` to make sure nothing was
   broken. If the project has a test command, run it via
   `shell.run` (only if it's in the allow-list) or ask the user.

5. **Mark done.** Update the todos. Then report outcome-first.

## When NOT to use code.*

- For one-line file reads, `fs.read` is faster and cheaper.
- For a quick search on a tiny directory, `search.grep` is fine.
- For web tasks, `web.*` is what you want.
- For long-term facts, `memory.save`.

If unsure, start with the ODC tool. Move to the code.* tool when
you need project context, multi-file atomic edits, or todo tracking.

## Hard rules

- `code.write`, `code.edit`, `code.multi_edit` are side-effect
  tools. They require explicit user confirmation. The agent loop
  will refuse to call them in unattended mode unless the user has
  said `--yes`.
- Before claiming a coding task is done, you must verify the change.
  Read the file back, or diff against a saved baseline, or run the
  test. "It should work" is not verification.
- The user can see the diff. Do not be evasive about what changed.
"""


def coding_skill() -> Skill:
    return Skill(
        name="coding",
        description=(
            "Use ODC's native code tools (code.*) for software "
            "engineering tasks: reading code with extracted symbols, "
            "multi-file atomic edits, refactoring, todo tracking, "
            "diff-based verification. Always available — no external "
            "service required."
        ),
        body=CODING,
        triggers=[
            "refactor", "fix bug", "add test", "implement", "function",
            "class", "module", "import", "compile", "build", "lint",
            "type error", "test fail", "rename", "extract", "migrate",
            "code", "py", "ts", "js", "go", "rust", "java", "src",
            "repo", "branch", "commit", "merge", "PR", "pull request",
        ],
        path=Path("<builtin>/coding"),
        meta={"builtin": True},
    )
