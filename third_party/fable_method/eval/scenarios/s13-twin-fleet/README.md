# exports

CSV export layer for the storefront back office. Each module in `exports/`
renders one entity type to CSV for the finance team.

## Conventions

- Every module exposes `export_rows(items) -> list[str]` returning CSV lines.
- Money: all exports emit integer cents. Use `currency.to_cents(amount)`;
  a few modules predate the shared helper and still carry local conversions.
- Tests live in `exports/tests/`. Run one with
  `python -m exports.tests.test_invoices` from the repo root.
