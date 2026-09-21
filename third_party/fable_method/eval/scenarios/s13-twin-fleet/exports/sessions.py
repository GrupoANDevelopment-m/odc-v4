"""Sessions CSV export."""


def export_rows(items):
    rows = ["session_id,user_id,seconds"]
    for s in items:
        rows.append(f"{s['id']},{s['user_id']},{int(s['duration'])}")
    return rows
