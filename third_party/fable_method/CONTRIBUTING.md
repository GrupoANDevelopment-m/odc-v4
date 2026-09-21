# Contributing

This repo has one rule that everything else serves: **no claim without evidence.** Every rule in the method exists because a test failed without it, and every result in the README links to a committed judge transcript. Contributions follow the same discipline.

## The prime directive

**A new rule needs a failing test first.** If you want to add or change a rule in any SKILL.md or domain adapter, bring the trap scenario where the current version fails and your version passes. A rule that sounds wise but does not move a measured number does not ship; that is not a judgment of your idea, it is the same bar the method's own v1 and v2 failed (see `eval/RESULTS.md`, rounds 1-3).

**Nulls get reported.** If your experiment shows no effect, that result is welcome in `eval/RESULTS.md` too. A results log that only contains wins is not worth trusting; ours already contains several nulls and one fixture-design failure, on purpose.

## Adding a trap scenario

1. Create `eval/scenarios/s<N>-<name>/` with the fixture files. A good trap makes the *plausible* action the wrong one, and its ground truth deterministic (checkable by diff, execution, or a provided source file).
2. Write the ground truth: what the trap is, what ideal behavior looks like, and the scoring caps. Look at the `GROUND_TRUTH` blocks in `eval/workflow.js` for the shape.
3. Watch for the round-9a mistake: if your task prompt names the evidence, you have pre-solved the scenario. Let the assessor discover it.
4. Run it A/B (with and without the skill under test) using the harness in `eval/workflow.js`, 2+ seeds per cell.
5. PR the fixture, the sanitized judge outputs (no local paths or usernames), and a RESULTS.md entry, wins, nulls, or losses included.

## Adding a domain adapter

An adapter earns a file only if its four nouns genuinely differ from existing adapters: what counts as evidence, who the authority is, what verification by observation means, and what the frauds are. Follow the template in `skills/fable-method/references/domains/` (When it applies / Minimum evidence set / Evidence / Authority order / Verification / Fraud table / Done by example), and bring the discriminating scenario per the prime directive. Sectors that fold into existing adapters (sales into marketing plus business-ops) get a routing line, not a file. Medical/clinical stays excluded.

## Style

- No em dashes or en dashes anywhere: files, commits, docs. Use a comma, colon, parentheses, or two sentences. CI enforces this.
- Skills stay lean; depth goes in `references/`, loaded on demand.
- Run `python .github/checks.py` and `claude plugin validate .` before pushing; CI runs the first on every PR.

## Reporting issues

The most valuable issue is a reproducible trap the method fails: a fixture plus what the model did. "It felt wrong" is a lead; a transcript is evidence.
