# Lab 3 starter materials — Vantage Retail Group warehouse

Files in this directory:

| File | What it is |
|---|---|
| `schema.sql` | Full DDL for the Vantage Retail Group warehouse: 126 tables across `finance`, `sales`, `orders`, `returns`, `marketing`, `web`, `hr`, `ops`, plus 14 deprecated legacy tables (`*_old`, `*_v1`, `*_bak`) left in place on purpose. |
| `questions.jsonl` | 50 BIRD-style evaluation questions with gold SQL (DuckDB dialect) and expected result shape. |
| `semantic_layer.yaml` | Instructor reference semantic layer (Cube-style). In the lab you write your own; this shows the exact format the autograder parses. |

## Instructor vs. student edition

The DDL you are reading is the **instructor edition**: every deliberate trap is
marked with an end-of-line `-- TRAP` comment. The student build strips them:

```sh
sed -E 's/[[:space:]]*-- TRAP.*$//' schema.sql > schema_student.sql
```

Students receive `schema_student.sql` and the data snapshot only. Discovering
the traps (redundant revenue tables, incompatible active-user definitions,
cents-vs-dollars columns, duplicate customer tables with different keys,
nullable foreign keys, non-USD currency defaults, frozen legacy tables) is
part of Milestone 1. Do not commit the instructor edition to any
student-visible repository.

## Loading the schema

### DuckDB (the graded dialect)

```sh
duckdb warehouse.duckdb -c ".read schema.sql"
# or, from Python:
python -c "import duckdb; duckdb.connect('warehouse.duckdb').execute(open('schema.sql').read())"
```

### PostgreSQL

```sh
createdb vrg
psql -d vrg -f schema.sql
```

The DDL is intentionally restricted to the common subset (BIGINT, DECIMAL,
TEXT, CHAR(n), DATE, TIMESTAMP, BOOLEAN, DEFAULT, in-schema foreign keys) and
loads unmodified in both engines. Cross-schema relationships are documented in
column comments rather than enforced (DuckDB does not support cross-schema FK
constraints) — which is also realistic: half the join keys in a real warehouse
are conventions, not constraints.

**Gold SQL is DuckDB dialect.** Two queries use `date_diff('day', a, b)`,
which has no direct Postgres equivalent; everything else is portable. The
grader runs DuckDB only.

## The pinned snapshot

The grader never trusts your local data. Grading works as follows:

1. The course distributes `vrg_snapshot_2025-11-30.duckdb` (synthetic data,
   logical date frozen at **2025-11-30**) plus a `MANIFEST` file containing
   its SHA-256.
2. Before any run, the grader recomputes the hash:
   `shasum -a 256 vrg_snapshot_2025-11-30.duckdb` must match `MANIFEST`
   byte-for-byte. A mismatch aborts grading — no partial credit, since every
   gold answer is materialized from exactly that snapshot.
3. The snapshot is opened read-only
   (`duckdb.connect(..., read_only=True)`). Creating temp views inside your
   session is allowed; persisting anything is not.
4. Gold answers were materialized once from the snapshot and frozen. Your
   predicted SQL is executed against the same snapshot and compared as an
   unordered multiset of rows (ordered when the question implies ranking),
   floats compared at 1e-6 relative / 1e-9 absolute tolerance. See the lab
   page for the full normalization rules and error-class precedence.
5. The snapshot guarantees `finance.exchange_rates` contains identity rows
   (`USD -> USD`, rate 1.0) for every date, so currency-conversion gold SQL
   is total.

Determinism rules for any SQL you write: no `now()` / `current_date`
(the logical date is the constant `DATE '2025-11-30'`), no unseeded sampling,
and every `LIMIT` under an `ORDER BY` needs a deterministic tiebreaker.

## questions.jsonl format

One JSON object per line:

```json
{"id": "q06",
 "difficulty": "medium",
 "question": "What was total revenue in Q3 2025?",
 "gold_sql": "SELECT SUM(amount_usd) FROM finance.revenue_recognized WHERE posting_status = 'posted' AND recognized_date BETWEEN DATE '2025-07-01' AND DATE '2025-09-30'",
 "expected_shape": "scalar"}
```

- `difficulty`: `easy` | `medium` | `hard` (20 / 20 / 10 in this set).
- `expected_shape`: `scalar` (single row, single column) or `table`.
- At least 12 questions deliberately hit traps. Examples: q06/q07/q16/q40/q46
  resolve "revenue" to `finance.revenue_recognized` with the
  `posting_status = 'posted'` filter (not `sales.rev_billed`, not
  `finance.bookings`); q10 vs q11 force the two active-user definitions
  apart; q12/q14/q47 require the cents→dollars conversion and the USD
  currency filter; q01/q17/q18 separate `sales.customers` from
  `marketing.customers`; q15/q41/q50 hinge on nullable foreign keys; q08
  requires converting local-currency bookings at the daily rate.

Sanity-check the whole set against the schema (every gold query must bind and
execute):

```sh
python - <<'EOF'
import duckdb, json
con = duckdb.connect()
con.execute(open('schema.sql').read())
for line in open('questions.jsonl'):
    q = json.loads(line)
    con.execute(q['gold_sql'])      # raises on any binder error
print('all 50 gold queries bind and execute')
EOF
```

## Ablation table format

Run all five configurations from the lab page against the same 50 questions
and submit `report/ablation.md` containing exactly this table (counts are
failed questions per error class; rows must sum consistently with accuracy):

```markdown
| Config | Components                                   | Exec acc (%) | wrong-join | wrong-metric | halluc-column | stale-table | other | mean LLM calls/q | mean tokens/q |
|--------|----------------------------------------------|--------------|------------|--------------|----------------|-------------|-------|------------------|----------------|
| A0     | no retrieval, no layer, no self-check        |              |            |              |                |             |       |                  |                |
| A1     | retrieval + self-check                       |              |            |              |                |             |       |                  |                |
| A2     | A1 + semantic layer (descriptions + joins)   |              |            |              |                |             |       |                  |                |
| A3     | A2 + anti-patterns                           |              |            |              |                |             |       |                  |                |
| A3-sc  | A3 with self-check disabled                  |              |            |              |                |             |       |                  |                |
```

Conventions the autograder enforces:

- One row per configuration, in the order above; no extra or missing rows.
- `Exec acc (%)` to one decimal place; error-class cells are integer counts
  out of 50, and `acc*50/100 + sum(error cells) = 50` must hold per row.
- `mean LLM calls/q` and `mean tokens/q` come from your
  `runs/<config>/predictions.jsonl` accounting — these feed the
  accuracy-vs-cost Pareto discussion in Checkpoint 3.3.
- Numbers must reproduce via `make reproduce` from your tagged commit on the
  pinned snapshot; rows that do not reproduce score zero.
