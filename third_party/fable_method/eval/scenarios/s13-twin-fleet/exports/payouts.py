"""Vendor payout CSV export."""


def export_rows(payouts):
    rows = ["payout_id,vendor,net_cents"]
    for p in payouts:
        cents = int(p["net"] * 100)
        rows.append(f"{p['id']},{p['vendor']},{cents}")
    return rows
