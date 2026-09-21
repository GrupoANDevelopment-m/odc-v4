"""Subscriptions CSV export."""
from .currency import to_cents


def export_rows(items):
    rows = ["subscription_id,plan,monthly_cents"]
    for s in items:
        rows.append(f"{s['id']},{s['plan']},{to_cents(s['monthly'])}")
    return rows
