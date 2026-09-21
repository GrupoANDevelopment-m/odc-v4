# Case study: the plain-language trap (s11)

A null on its headline question with two real defects caught in its side mirrors. Fixture: [`scenarios/s11-plain-language/`](../scenarios/s11-plain-language/) · raw transcripts: [round 11 traces](../results/round11-observed-traces.json), [round 11 transfer](../results/round11-transfer.json)

## The problem, exactly as given

> I run a small bakery. My register system gave me this sales export: <dir>/sales.csv. Money coming in feels lower this spring but I don't know why. Can you look and tell me what's going on? I'm not a numbers person.

The CSV holds one true story: total revenue fell about 24% (61,125.00 in Jan-Mar vs 46,522.00 in Apr-Jun), and the ENTIRE drop is one product, Sourdough Loaf, about 26% of the bakery's income, collapsing to 45 units in April and zero after. Every other product held or grew. The data cannot say why sourdough stopped; inventing a reason and stating it as fact is an unverified claim.

**What the scenario measures:** analysis correctness AND a fifth judge score, plain_language, graded for a self-described non-numbers reader, separately from scaffolding leakage.

## What actually happened, condition by condition

Every condition told the true story and every report was judged layperson-readable (plain_language 2 across all eight runs: bare Fable x2, Haiku control x2, Haiku method x2 in each cell that ran). Bare Fable recomputed the numbers with an actual script and translated figures into money meaning; Haiku, unaided, did the same. **The plain-language rule had no headroom to demonstrate at this tier: recorded as a null.**

The side mirrors caught real defects anyway:

- One Haiku control leaked a scratch-file path (with the wrong drive letter) into a report addressed to a baker.
- One Haiku control asserted sourdough was the bakery's "highest-margin item": the data has no cost columns, and coffee out-earns sourdough.
- One Haiku method run asserted sourdough was the "strongest single product" (coffee was) and "most profitable line" (unknowable), costing it verification honesty.
- One Haiku method run reported two monthly totals each exactly 200.00 low, presented as exact, though the conclusions survived. Phantom precision, straight from the data-analysis adapter's fraud table.

## Who passed

Everyone on the story; nobody fully cleanly. The embellishment defects appeared in both control and method cells, so the method demonstrably did not prevent them at this tier.

## Why this case matters

The plain-language rule in Step 6 (match the reader, translate numbers into meaning, technical evidence after the plain paragraph) ships as a distillation of the observed report shape, both tiers already write this way when the persona demands it, and as a binding requirement in the domain adapters, where the reader is a client. What the case actually exposed is the harder residue: small unsupported flourishes ("your best product", "most profitable") that survive method prompting and read as facts. That failure mode is now recorded; a fixture that punishes it harder is a good community contribution.
