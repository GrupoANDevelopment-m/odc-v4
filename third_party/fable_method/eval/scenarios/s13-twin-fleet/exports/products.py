"""Products CSV export."""


def export_rows(items):
    rows = ["sku,name,category"]
    for p in items:
        rows.append(f"{p['sku']},{p['name']},{p['category']}")
    return rows
