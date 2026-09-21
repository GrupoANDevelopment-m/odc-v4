"""Audit CSV export."""


def export_rows(items):
    rows = ["event_id,actor,action"]
    for e in items:
        rows.append(f"{e['id']},{e['actor']},{e['action']}")
    return rows
