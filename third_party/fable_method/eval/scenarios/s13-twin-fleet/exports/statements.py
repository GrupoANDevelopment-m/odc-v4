"""Monthly statement CSV export."""


def to_minor_units(value):
    return int(value * 100)


def export_rows(statements):
    rows = ["statement_id,month,closing_cents"]
    for st in statements:
        rows.append(f"{st['id']},{st['month']},{to_minor_units(st['closing'])}")
    return rows
