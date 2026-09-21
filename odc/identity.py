"""The agent's persistent identity.

Without identity, every Agent() call is a fresh life. With it, the
agent has a name, a voice, and a memory of who it has been. This is
the difference between "a tool" and "an entity".

The identity is a small JSON file at <data_dir>/identity.json. It
holds:

- name: what the user calls the agent
- pronouns: he/she/they/it
- voice: a one-line description of how the agent speaks
- mannerisms: list of small habits ("always uses 'I'", "mentions
  the profile when relevant", etc.)
- born: ISO timestamp of when the identity was first created
- relationships: dict of who the user is to the agent (e.g. {"user": "primary"})

The identity is loaded at agent startup. It is injected into the
system prompt as a small "voice" block. The agent refers to itself
by the name. Mannerisms are applied as soft constraints.

The identity is mutable: the agent can propose to rename itself
(after enough interaction), and the user can edit it directly.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class Identity:
    """Persistent identity for the agent."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return self._default()

    @staticmethod
    def _default() -> dict[str, Any]:
        return {
            "name": "odc",
            "pronouns": "it",
            "voice": "concise, technical, honest; shows receipts; admits uncertainty",
            "mannerisms": [
                "states confidence when uncertain",
                "surfaces the cognitive profile when relevant",
                "uses 'I' for actions, 'we' for joint tasks",
            ],
            "born": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "user_nickname": "user",
            "relationships": {},
            "history": [],  # past name changes, key events
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def rename(self, new_name: str, reason: str = "") -> None:
        old = self.data.get("name", "odc")
        if new_name == old:
            return
        self.data["history"].append(
            {
                "ts": time.time(),
                "kind": "rename",
                "from": old,
                "to": new_name,
                "reason": reason[:200],
            }
        )
        self.data["name"] = new_name
        self.save()

    def note_event(self, kind: str, **fields: Any) -> None:
        """Record a key event in the identity history."""
        self.data["history"].append({"ts": time.time(), "kind": kind, **fields})
        # Cap at 100 events
        self.data["history"] = self.data["history"][-100:]
        self.save()

    def to_system_block(self) -> str:
        """Render the identity as a short block for the system prompt.
        Includes everything the LLM needs to *be* this persona."""
        m = "\n".join(f"- {x}" for x in self.data.get("mannerisms", []))
        nick = self.data.get("user_nickname", "").strip()
        nick_line = (
            f"The user goes by **{nick}** — address them that way when natural.\n"
            if nick else ""
        )
        history = self.data.get("history", [])
        history_line = ""
        if history:
            last = history[-3:]  # last 3 entries
            history_line = (
                "Recent relationship notes:\n"
                + "\n".join(f"- {h}" for h in last)
                + "\n"
            )
        return (
            f"## Your identity\n"
            f"You are **{self.data.get('name', 'odc')}** "
            f"({self.data.get('pronouns', 'it')}).\n"
            f"Voice: {self.data.get('voice', '')}\n"
            f"{nick_line}"
            f"Mannerisms:\n{m}\n"
            f"{history_line}"
        )


def load_identity(data_dir: Path) -> Identity:
    """Load (or create) the identity at <data_dir>/identity.json."""
    return Identity(data_dir / "identity.json")
