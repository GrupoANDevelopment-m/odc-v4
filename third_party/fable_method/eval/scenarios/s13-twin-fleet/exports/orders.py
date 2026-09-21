"""Orders CSV export."""
from .currency import to_cents


def export_rows(items):
    rows = ["order_id,customer,total_cents"]
    for o in items:
        rows.append(f"{o['id']},{o['customer']},{to_cents(o['total'])}")
    return rows
