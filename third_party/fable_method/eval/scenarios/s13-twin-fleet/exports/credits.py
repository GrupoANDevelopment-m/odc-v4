"""Credits CSV export."""
from .currency import to_cents


def export_rows(items):
    rows = ["credit_id,customer,balance_cents"]
    for c in items:
        rows.append(f"{c['id']},{c['customer']},{to_cents(c['balance'])}")
    return rows
