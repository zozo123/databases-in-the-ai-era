#!/usr/bin/env python3
"""DATA 2027 · Lab 3 grader stub.

Loads the Vantage Retail Group schema into an in-memory DuckDB, then grades a
student answers file against the gold queries in questions.jsonl using
execution-accuracy semantics: both SQL statements are executed against the
same pinned database and their result sets compared (unordered multiset
comparison unless the question implies ranking; scalars compared with a 1e-6
relative tolerance).

Usage:
    python3 grade.py --answers my_answers.jsonl [--questions ../questions.jsonl]
                     [--schema ../schema.sql] [--data warehouse.duckdb]

Answers file format (one JSON object per line):
    {"id": "q01", "sql": "SELECT ..."}

Without --data, queries run against the empty schema: this checks that the
SQL *binds* (no missing tables/columns) and that its column shape matches the
gold query, which is exactly what the four example test cases below assert.
The instructor edition swaps in the pinned snapshot, at which point result
comparison becomes meaningful. Grade classes reported per question:

    PASS            results match gold (or, schema-only mode: binds + shape)
    WRONG_RESULT    executes, results differ from gold
    BINDER_ERROR    references a table/column that does not exist
    STALE_TABLE     references a frozen legacy table (*_old, *_v1, *_bak)
    EXEC_ERROR      any other execution failure
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

try:
    import duckdb
except ImportError:
    sys.exit("pip install duckdb  (the grader has exactly one dependency)")

HERE = Path(__file__).resolve().parent
STALE = re.compile(r"\b\w+_(?:old|v1|bak)\b", re.IGNORECASE)


def load_db(schema_path: Path, data_path: Path | None):
    if data_path:
        return duckdb.connect(str(data_path), read_only=True)
    con = duckdb.connect(":memory:")
    sql = schema_path.read_text()
    # strip instructor-edition trap annotations if present
    sql = re.sub(r"--\s*TRAP.*", "", sql)
    con.execute(sql)
    return con


def run(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def rows_equal(a, b, ordered):
    def norm(v):
        if isinstance(v, float):
            return round(v, 6)
        return v

    na = [tuple(norm(v) for v in r) for r in a]
    nb = [tuple(norm(v) for v in r) for r in b]
    if ordered:
        return na == nb
    return sorted(map(repr, na)) == sorted(map(repr, nb))


def scalar_close(a, b):
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    if fb == 0:
        return abs(fa) < 1e-9
    return math.isclose(fa, fb, rel_tol=1e-6)


def grade_one(con, q, student_sql, schema_only):
    if STALE.search(student_sql):
        return "STALE_TABLE"
    try:
        s_cols, s_rows = run(con, student_sql)
    except (duckdb.BinderException, duckdb.CatalogException):
        return "BINDER_ERROR"
    except duckdb.Error:
        return "EXEC_ERROR"
    g_cols, g_rows = run(con, q["gold_sql"])
    if schema_only:
        return "PASS" if len(s_cols) == len(g_cols) else "WRONG_RESULT"
    if q.get("expected_shape") == "scalar":
        ok = (
            len(s_rows) == 1
            and len(g_rows) == 1
            and scalar_close(s_rows[0][0], g_rows[0][0])
        )
        return "PASS" if ok else "WRONG_RESULT"
    ordered = bool(re.search(r"\border\s+by\b", q["gold_sql"], re.IGNORECASE))
    return "PASS" if rows_equal(s_rows, g_rows, ordered) else "WRONG_RESULT"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    ap.add_argument("--questions", default=str(HERE.parent / "questions.jsonl"))
    ap.add_argument("--schema", default=str(HERE.parent / "schema.sql"))
    ap.add_argument("--data", default=None, help="pinned snapshot (instructor)")
    args = ap.parse_args()

    questions = {
        q["id"]: q
        for q in map(json.loads, open(args.questions, encoding="utf-8"))
    }
    answers = {
        a["id"]: a["sql"]
        for a in map(json.loads, open(args.answers, encoding="utf-8"))
    }
    con = load_db(Path(args.schema), Path(args.data) if args.data else None)
    schema_only = args.data is None

    tally = {}
    for qid, q in sorted(questions.items()):
        verdict = "MISSING" if qid not in answers else grade_one(
            con, q, answers[qid], schema_only
        )
        tally[verdict] = tally.get(verdict, 0) + 1
        print(f"{qid}  {q['difficulty']:<7}  {verdict}")

    total = len(questions)
    passed = tally.get("PASS", 0)
    mode = "schema-only (bind + shape)" if schema_only else "execution accuracy"
    print(f"\n{passed}/{total} PASS  [{mode}]  breakdown: {tally}")


# --- four example test cases (run: python3 grade.py --self-test) -----------

def self_test():
    con = load_db(HERE.parent / "schema.sql", None)
    q = {"gold_sql": "SELECT 1 AS x", "expected_shape": "scalar"}
    assert grade_one(con, q, "SELECT 1", False) == "PASS"
    assert grade_one(con, q, "SELECT 2", False) == "WRONG_RESULT"
    assert grade_one(con, q, "SELECT * FROM no_such_table", False) == "BINDER_ERROR"
    assert (
        grade_one(con, q, "SELECT * FROM finance.revenue_recognized_v1", False)
        == "STALE_TABLE"
    )
    print("self-test: 4/4 example cases pass")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        main()
