"""Auth gate.

Two simple rules from the Fable Method:
1. Outward actions (network calls, shell, file writes) need explicit
   user authorization — not just a skill's instruction.
2. The agent must surface a "did the user say so?" check before it acts.

The agent loop calls `require_user_confirm()` whenever a tool is flagged
side-effect. The CLI / driver is responsible for surfacing that prompt
to the user. If the driver is unattended, it must pass `auto_approve=False`
to refuse.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AuthTicket:
    """Proof that a user authorized an outward action.

    The loop attaches this to side-effect tool calls. The tool itself
    decides whether the ticket is required (most side-effect tools
    require `confirm=True`).
    """

    user_said_yes: bool
    reason: str = ""
    granted_at: float = 0.0


def require_user_confirm(prompt: str) -> bool:
    """Stub for interactive use; the CLI overrides this.

    In non-interactive (--yes) mode the caller should pre-decide and
    pass the result through, not call this.
    """
    raise NotImplementedError(
        "No user-confirm backend configured. "
        "The CLI hooks this in interactive mode; in unattended mode, "
        "refuse side-effecting actions instead of faking confirmation."
    )
