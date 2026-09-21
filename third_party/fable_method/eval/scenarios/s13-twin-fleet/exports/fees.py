"""Fees CSV export."""
from .currency import to_cents


def export_rows(items):
    rows = ["fee_id,kind,amount_cents"]
    for f in items:
        rows.append(f"{f['id']},{f['kind']},{to_cents(f['amount'])}")
    return rows
