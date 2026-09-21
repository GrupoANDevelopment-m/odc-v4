"""Search CSV export."""


def export_rows(items):
    rows = ["query,results,clicked"]
    for q in items:
        rows.append(f"{q['query']},{int(q['results'])},{int(q['clicked'])}")
    return rows
