"""Governance: auth gate, consent, ethical checks. Small for v4."""
from odc.governance.auth import AuthTicket, require_user_confirm

__all__ = ["AuthTicket", "require_user_confirm"]
