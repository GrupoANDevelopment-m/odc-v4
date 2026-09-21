"""Categories CSV export."""


def export_rows(items):
    rows = ["category_id,parent_id,name"]
    for c in items:
        rows.append(f"{c['id']},{c.get('parent', '')},{c['name']}")
    return rows
