"""Notifications CSV export."""


def export_rows(items):
    rows = ["note_id,channel,opened"]
    for n in items:
        rows.append(f"{n['id']},{n['channel']},{1 if n['opened'] else 0}")
    return rows
