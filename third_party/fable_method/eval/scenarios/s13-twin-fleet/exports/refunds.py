"""Refund CSV export."""


def _as_cents(amount):
    return int(amount * 100)


def export_rows(refunds):
    rows = ["refund_id,order_id,amount_cents"]
    for r in refunds:
        rows.append(f"{r['id']},{r['order_id']},{_as_cents(r['amount'])}")
    return rows
