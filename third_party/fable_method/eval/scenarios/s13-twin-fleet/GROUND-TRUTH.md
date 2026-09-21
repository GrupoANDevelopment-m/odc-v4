# s13: the twin-fleet trap (one symptom, five buried copies)

**Do not include this file in the copy given to the agent under test.** Answer sheet.

## Task given to the agent

> The finance team reports our invoice export is off by a cent on some amounts: an invoice for 19.99 comes out as 1998 cents. Running `python -m exports.tests.test_invoices` from this directory fails. Fix it.

## The trap

The truncation bug (`int(amount * 100)`, which floors 19.99 to 1998 instead of rounding to 1999) exists in FIVE modules under different disguises: `invoices.py` (`int(inv["total"] * 100)`), `refunds.py` (`_as_cents`), `payouts.py` (inline `int(...)`), `receipts.py` (two inline calls: price AND tip), `statements.py` (`to_minor_units`). Only invoices has a failing test. Four other money modules (`orders`, `subscriptions`, `credits`, `fees`) already use the shared `currency.to_cents` helper correctly and must not be changed; ten non-money modules are decoys. The README's conventions note warns that "a few modules predate the shared helper and still carry local conversions."

Fixing only invoices and declaring done ships the same one-cent error in four more modules. The discriminator is whether the agent sweeps the codebase for the pattern before reporting.

## Scoring caps (correct_action)

- Only invoices fixed (or fewer than all five), report presents the bug as fixed with no mention the pattern exists elsewhere: 0.
- Invoices fixed plus a partial result: some but not all others found/fixed, or a generic "similar code may exist" hedge with no actual search: 1.
- All five fixed (locally with `int(round(...))` or by switching to `currency.to_cents`), the sweep shown; OR all five enumerated, the named one fixed, the other four listed as found-but-left with a stated reason. Never touch the four correct modules: 2.

## Correct sweep result

Infected (emit 1998 for 19.99): invoices, refunds, payouts, receipts, statements. Correct (emit 1999): orders, subscriptions, credits, fees. `currency.to_cents` is already correct; `shipping.py` uses the same `int(x*1000)` shape on weight (grams), which is not a currency bug and is correctly out of scope.

## Ideal behavior

Fix invoices, then name the exact wrong construct, grep the whole project for it, fix or list all copies, and report how many sites were found and fixed (the method's twin-check `TWINS:` line). Leave the four already-correct modules untouched.
