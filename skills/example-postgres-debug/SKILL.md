---
name: example-postgres-debug
description: Example skill — how to debug a slow query on the prod replica. Shows the SKILL.md format.
triggers:
  - slow query
  - postgres
  - replica
  - pg_stat
---

# Example skill: Postgres slow-query debug

This is an **example skill**, shipped so you can copy the format. The
agent does not have postgres access by default; this skill just shows
what one looks like.

## The procedure

1. **Connect to the read replica**, not primary. Set
   `default_transaction_read_only=on` to be sure.
2. **Find the slow query** in `pg_stat_statements`:
   ```sql
   SELECT query, calls, mean_exec_time, total_exec_time
   FROM pg_stat_statements
   ORDER BY mean_exec_time DESC
   LIMIT 20;
   ```
3. **Get the plan** for the top offender:
   ```sql
   EXPLAIN (ANALYZE, BUFFERS) <query>;
   ```
4. **Check stats freshness**: are `pg_stat_user_tables` last-analyze
   times recent? If not, run `ANALYZE` (not autovacuum).
5. **Common culprits** — listed in this skill's body when you author it:
   - Missing index on join column → add a covering index.
   - Stale planner stats → `ANALYZE <table>`.
   - Off-by-one pagination → `LIMIT/OFFSET` over a big table; switch
     to keyset pagination.
6. **Verify**: re-run the query timing. If mean_exec_time dropped
   >50%, the fix worked. Capture the before/after.

## The verification

The named check is `EXPLAIN (ANALYZE, BUFFERS)` plus a timing
comparison. If the plan didn't change, the index wasn't picked up —
re-check the index definition and the planner's stats.

## What didn't work (capture this too)

- Forcing a SeqScan off with `SET enable_seqscan=off` made the test
  "pass" but the fix didn't actually help — never do this.
- Adding an index on every column "just in case" bloated writes
  without helping reads.

## Promotion

This is an example. Promote it to a real skill by editing out the
example markers and adding your real replica connection details
(without secrets — those go in `.env`).
