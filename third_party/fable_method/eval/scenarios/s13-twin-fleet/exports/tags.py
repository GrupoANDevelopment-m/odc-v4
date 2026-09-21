"""Tags CSV export."""


def export_rows(items):
    rows = ["tag_id,label"]
    for t in items:
        rows.append(f"{t['id']},{t['label']}")
    return rows
