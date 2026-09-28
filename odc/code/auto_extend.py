"""Auto-extension gate — runtime toggle for self-extension.

The web/UI writes the user's preference to <data_dir>/config.json.
The agent reads this gate at start (and on each tool invocation)
to decide whether dynamic.tool_create, dynamic.skill_create,
dynamic.tool_repair, and dynamic.tool_load should be allowed.

Default values (all True, EXCEPT require_safety which is ALWAYS on):
  - tool_create    : True
  - skill_create   : True
  - tool_repair    : True
  - tool_load      : True
  - require_safety : True  -- CANNOT be disabled (system invariant)

Why this exists:
  - User wanted auto-extension ON by default (so agent can extend itself)
  - But with an explicit toggle in UI to disable specific gates
  - Even with all gates off, the AST safety check is enforced — that's
    a constitutional invariant (I6)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Module-level gate state — shared across threads
_gate: dict[str, bool] = {
    "tool_create": True,
    "skill_create": True,
    "tool_repair": True,
    "tool_load": True,
    "require_safety": True,  # ALWAYS True — system invariant
}


def reset_to_defaults() -> None:
    """Reset all gates to defaults. require_safety cannot be turned off."""
    _gate.clear()
    _gate.update({
        "tool_create": True,
        "skill_create": True,
        "tool_repair": True,
        "tool_load": True,
        "require_safety": True,
    })


def set_gate(key: str, value: bool) -> bool:
    """Set a single gate. Returns True if accepted, False if rejected."""
    if key not in _gate:
        return False
    # require_safety is unbreakable
    if key == "require_safety" and not value:
        return False
    _gate[key] = bool(value)
    return True


def get_gate(key: str) -> bool:
    return _gate.get(key, True)


def all_gates() -> dict[str, bool]:
    """Return all current gate values."""
    return dict(_gate)


def is_allowed(tool_name: str) -> bool:
    """Check if a tool invocation is allowed under current gates.

    Mapping:
      - dynamic.tool_create   -> tool_create
      - dynamic.skill_create  -> skill_create
      - dynamic.tool_repair   -> tool_repair
      - dynamic.tool_load     -> tool_load
      - everything else       -> always allowed
    """
    mapping = {
        "dynamic.tool_create":  "tool_create",
        "dynamic.skill_create": "skill_create",
        "dynamic.tool_repair":  "tool_repair",
        "dynamic.tool_load":    "tool_load",
    }
    key = mapping.get(tool_name)
    if key is None:
        return True
    return _gate.get(key, True)


def load_from_config(path: Path) -> dict[str, bool]:
    """Load gate state from the system config file.

    Falls back to defaults if file is missing or invalid.
    Returns the final gate state (after applying config).
    """
    reset_to_defaults()
    if not path.exists():
        return all_gates()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ae = data.get("auto_extend", {})
        for k, v in ae.items():
            set_gate(k, bool(v))
    except Exception:
        pass
    return all_gates()


def save_to_config(path: Path) -> None:
    """Persist current gate state to <data_dir>/config.json under
    the auto_extend key. Preserves other config sections."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["auto_extend"] = all_gates()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
