"""Point-of-sale receipt CSV export."""


def export_rows(receipts):
    rows = ["receipt_id,register,price_cents,tip_cents"]
    for rc in receipts:
        rows.append(
            f"{rc['id']},{rc['register']},{int(rc['price'] * 100)},{int(rc['tip'] * 100)}"
        )
    return rows
