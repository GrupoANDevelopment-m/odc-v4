"""Inventory CSV export."""


def export_rows(items):
    rows = ["sku,warehouse,quantity"]
    for i in items:
        rows.append(f"{i['sku']},{i['warehouse']},{int(i['quantity'])}")
    return rows
