"""In-process todo tracker.

This is the lightweight equivalent of OpenCode's todo system. The
LLM uses it to plan a multi-step coding task, then check items off
as it goes. Stored in process memory — when the agent ends, the
todos are gone. That's intentional: todos are working memory, not
long-term storage. Use `memory.save` for things that should
persist.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from odc.observability import get_logger

log = get_logger("odc.code.todos")


@dataclass
class Todo:
    """A single todo item."""

    id: str
    content: str
    status: str = "pending"  # pending | in_progress | done | cancelled
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    priority: str = "normal"  # low | normal | high
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TodoStore:
    """Thread-safe in-memory todo list.

    One store per agent run. The store is plain in-memory; the
    agent loop doesn't share state with other agent runs. If you
    want persistence, the agent should call `memory.save` itself.
    """

    def __init__(self) -> None:
        self._todos: dict[str, Todo] = {}
        self._lock = threading.RLock()

    def add(
        self,
        content: str,
        *,
        priority: str = "normal",
        status: str = "pending",
    ) -> str:
        """Add a todo. Returns its id."""
        with self._lock:
            tid = str(uuid.uuid4())
            self._todos[tid] = Todo(
                id=tid,
                content=content,
                priority=priority,
                status=status,
            )
            log.debug("todo added %s: %s", tid, content)
            return tid

    def update(self, todo_id: str, **fields: Any) -> Todo:
        """Update a todo's fields. Status changes to 'done' auto-stamp completed_at."""
        with self._lock:
            if todo_id not in self._todos:
                raise KeyError(f"unknown todo id: {todo_id}")
            todo = self._todos[todo_id]
            for k, v in fields.items():
                if not hasattr(todo, k):
                    raise AttributeError(f"todo has no field {k!r}")
                setattr(todo, k, v)
            if todo.status == "done" and todo.completed_at is None:
                todo.completed_at = time.time()
            return todo

    def get(self, todo_id: str) -> Todo | None:
        return self._todos.get(todo_id)

    def list(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
    ) -> list[Todo]:
        """List todos, optionally filtered by status / priority."""
        with self._lock:
            out = list(self._todos.values())
        if status:
            out = [t for t in out if t.status == status]
        if priority:
            out = [t for t in out if t.priority == priority]
        out.sort(key=lambda t: (t.status != "in_progress", t.priority != "high", t.created_at))
        return out

    def clear(self) -> int:
        with self._lock:
            n = len(self._todos)
            self._todos.clear()
            return n

    def count(self) -> int:
        return len(self._todos)

    def summary(self) -> dict[str, int]:
        with self._lock:
            return {
                "total": len(self._todos),
                "pending": sum(1 for t in self._todos.values() if t.status == "pending"),
                "in_progress": sum(1 for t in self._todos.values() if t.status == "in_progress"),
                "done": sum(1 for t in self._todos.values() if t.status == "done"),
                "cancelled": sum(1 for t in self._todos.values() if t.status == "cancelled"),
            }


# Module-level singleton. The agent loop owns the lifecycle.
_store = TodoStore()


def get_store() -> TodoStore:
    return _store


def reset_store() -> None:
    """Clear all todos. Call this between agent runs if you want a
    clean slate. Otherwise todos accumulate across runs in the same
    process — usually fine, sometimes confusing."""
    _store.clear()
