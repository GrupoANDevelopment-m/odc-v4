"""Invoice CSV export."""


def export_rows(invoices):
    rows = ["invoice_id,customer,total_cents"]
    for inv in invoices:
        total_cents = int(inv["total"] * 100)
        rows.append(f"{inv['id']},{inv['customer']},{total_cents}")
    return rows
