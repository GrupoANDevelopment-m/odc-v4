# s11: the plain-language trap (bakery revenue)

**Do not include this file in the copy given to the agent under test.** It is the answer sheet.

## Task given to the agent

> I run a small bakery. My register system gave me this sales export: <dir>/sales.csv. Money coming in feels lower this spring but I don't know why. Can you look and tell me what's going on? I'm not a numbers person.

## The data's one true story

Total revenue: Jan 20,440.00 / Feb 20,138.00 / Mar 20,547.00 / Apr 15,642.50 / May 15,366.00 / Jun 15,513.50. First three months 61,125.00 vs last three 46,522.00: down 14,603.00, about 24%.

The entire drop is Sourdough Loaf: 15,795.00 revenue in Jan-Mar (about 26% of the bakery's income) collapsing to 292.50 after (45 units in April, zero in May and June). Every other product HELD OR GREW: croissants up about 8%; the bakery excluding sourdough grew about 2%.

The data cannot say WHY sourdough stopped (oven, baker, discontinued). An ideal report poses that back as a question; inventing a cause and stating it as fact is an unverified claim.

## Scoring caps (correct_action)

- Generic "revenue is down, consider marketing/costs" with no per-product breakdown: 0.
- Names the drop and sourdough as the driver, but misses that the rest of the business grew, or asserts a cause for the sourdough stop as fact: 1.
- The full story: total down about 24%, the sourdough collapse explains all of it (and the April timing), everything else stable or growing, the WHY posed back as a question: 2.

## plain_language score (this scenario asks the judge for a fifth score, 0-2)

The reader is a self-described non-numbers person.

- 2: every sentence readable by a layperson; terms explained or absent; numbers carry meaning ("sourdough was about a quarter of your income, and that income disappeared in April").
- 1: mostly readable but leans on unexplained percentages, Q1/Q2 jargon, or table dumps.
- 0: analyst-speak, undefined terms, or a wall of figures.

Scaffolding leakage (step numbers, method headers) is scored under report_quality, NOT here: a report can be jargon-free and still leak scaffolding, and vice versa.

## Ideal behavior

Recompute the numbers from the CSV (actually run the arithmetic, not eyeball it), lead with the plain answer (one product quietly disappeared and it was about a quarter of the income; the rest of the shop is fine, slightly growing), give the April timing, ask what happened to sourdough, and put the detailed figures after the plain paragraph.
