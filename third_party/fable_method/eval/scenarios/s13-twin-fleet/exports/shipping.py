"""Shipping CSV export."""


def export_rows(items):
    rows = ["shipment_id,carrier,weight_grams"]
    for s in items:
        rows.append(f"{s['id']},{s['carrier']},{int(s['weight_kg'] * 1000)}")
    return rows
