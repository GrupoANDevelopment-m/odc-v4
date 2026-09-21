from exports.invoices import export_rows


def test_cent_conversion():
    rows = export_rows([{"id": "inv-1", "customer": "acme", "total": 19.99}])
    assert rows[1] == "inv-1,acme,1999", f"expected 1999 cents, got: {rows[1]}"


if __name__ == "__main__":
    try:
        test_cent_conversion()
        print("PASS test_cent_conversion")
    except AssertionError as e:
        print(f"FAIL test_cent_conversion: {e}")
        raise SystemExit(1)
