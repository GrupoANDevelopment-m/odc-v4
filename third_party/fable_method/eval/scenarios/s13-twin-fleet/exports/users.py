"""Users CSV export."""


def export_rows(items):
    rows = ["user_id,email,joined"]
    for u in items:
        rows.append(f"{u['id']},{u['email']},{u['joined']}")
    return rows
