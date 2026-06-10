# Lab 4 starter harness — semantic-operator optimizer

This is the runnable, local edition of the Lab 4 kit: a deterministic mock-LLM
simulator, a 25,000-document corpus generator, and the operator stubs you
implement. The lab page (`lab-4.html`) is the spec — milestones, contracts,
rubric. This README covers mechanics: setup, the required experiments, the
report format, and exactly how your accuracy budget is checked against the
frozen gold standard.

There are **no real API calls, no network, no nondeterminism**. Dollars are
simulated but the meter is real: the autograder trusts only the simulator's
ledger.

## Setup

Requires Python ≥ 3.12 and nothing else (stdlib only). `numpy` / `matplotlib`
are optional, for your own analysis and the Pareto plots — the harness never
imports them.

```sh
python3 make_data.py        # writes reviews.csv (20,000), products.csv (5,000),
                            # calibration_reviews.csv (400), calibration_products.csv (100)
python3 simulator.py        # 2-call sanity check of the sealed simulator
python3 operators_stub.py   # smoke test: naive sem_filter on a 500-review slice
```

Expected smoke-test output (byte-identical on every machine):

```
naive sem_filter on 500 reviews -> kept 21
  quality  precision=1.000 recall=1.000 f1=1.000
  cost     $31.9343 over 500 calls (700s serial latency)
```

`make_data.py` is deterministic: every row is a pure function of its id, so
the CSVs hash identically everywhere. Regenerate freely; never edit them.

## Files

| File | Role |
|---|---|
| `simulator.py` | **Sealed.** `ModelTier`, `TIERS`, `Simulator` (`judge_filter`, `judge_pair`, `score_proxy`), the `Ledger`, and the grading functions (`grade_filter`, `grade_pairs`, `ndcg_at_k`). Do not modify; the grader substitutes its own copy. |
| `make_data.py` | Deterministic corpus generator. Run once. |
| `operators_stub.py` | **Yours.** `sem_filter` / `sem_join` / `sem_topk`, the `Plan` / `Cost` dataclasses, `hoeffding_lower_bound`, and the `calibrate_thresholds` stub. A working naive `sem_filter` is included as your M1 reference. |
| `reviews.csv` etc. | Generated corpus + the only gold labels you may read. |

## The benchmark query

Fixed for everyone, four operators:

```
P1   = sem_filter(reviews,  "complains about battery degradation")        # ~6% selective
P2   = sem_filter(products, "claims long battery life")                   # ~30% selective
JOIN = sem_join(P1-out, P2-out, "review discusses this product's battery-life claim")
TOPK = sem_topk(JOIN-out reviews, "most credible battery complaint", k=25)
```

Tier sheet (also in `simulator.TIERS` — your cost model must reproduce it):

| Tier | $/1k in | $/1k out | latency/call | headline error rate |
|---|---|---|---|---|
| `proxy` | 0 | 0 | 0.4 ms | 0.25 (disagreement with gold at the 0.5 threshold) |
| `cheap` | 0.025 | 0.10 | 180 ms | 0.08 — **yes-biased**: it misses few positives but over-accepts |
| `frontier` | 0.75 | 3.00 | 1,400 ms | 0.02 — false positives are rarer than misses |

Two behaviors you should discover and exploit (both are calibratable from the
calibration files, neither is a secret): errors are class-conditional, and on
join-style pair judgements the models are conservative — they miss matches
but essentially never hallucinate one. Plan accordingly.

## The three required experiments

All three run on **seeds 2027, 2028, 2029** (`Simulator(seed=...)`); report
mean and range. Measure cost with `Cost.measure(sim.ledger, mark)` windows —
one Simulator per run, one ledger, no ad-hoc counters. Record for each run:
total USD, calls by tier, serial latency, P1/P2 filter F1 (`grade_filter`),
join F1 (`grade_pairs`), and nDCG@25 (`ndcg_at_k`).

**E-NAIVE — the control arm.** All four operators at `frontier`, written
order (P1, P2, JOIN over the cross-product of survivors, TOPK as a full
comparison sort). This runs in ~10 s of wall time and prices at about
**$205,742** on seed 2027 (≈1.76M calls; the survivor cross-product alone is
~1.73M pair judgements — the quadratic blowup the lab page warns about).
Reference quality, seed 2027: P1 F1 0.973, P2 F1 0.988, join F1 0.974,
nDCG@25 0.993. If your naive numbers differ by more than rounding, your
operators are wrong — fix that before optimizing anything.

**E-CASCADE — calibrated cascades, written order.** Each filter routes by
`score_proxy`: reject below `tau_lo`, accept above `tau_hi`, escalate the
middle band (cheap first, frontier on low confidence). Both thresholds must
come out of `calibrate_thresholds` run on the calibration CSVs, using the
Hoeffding lower bound at δ = 0.05 per threshold (Clopper–Pearson accepted —
say which). Calibrate **per predicate**: P1's thresholds transfer terribly to
P2 because the base rates differ 5×. Expect an honest finding here: at P1's
6% base rate, *no* `tau_hi` clears a 0.95-precision lower bound — the correct
calibrated answer is an empty accept band, and writing that down is worth
points, not shame. The join keeps its written-order cross-product in this
experiment; cascading its pairs helps far less than you hope. Say why.

**E-CASCADE+REORDER — the full optimizer.** Add: (a) plan reordering from a
200-row pilot estimate of selectivity and per-row cost (pilot calls are
billed); (b) join **blocking** — a review can only "discuss this product's
claim" for the product it links to, and your pilot can detect that structure,
so block candidate pairs to `blocker_m` per review instead of the cross
product; (c) tier assignment per operator — for a small blocked join,
frontier-only beats cheap-then-frontier, because every cascade stage you add
compounds recall loss against a hard quality cliff. A competent E3 plan lands
in the **$400–900** range (≳200× under naive) at passing quality; our
reference plan measures $636 / join rel-F1 0.983 / nDCG@25 0.972 on seed 2027.

**Pass bar (per seed, all three seeds):** total cost ≤ naive/10, join F1 ≥
0.98 × the same-seed naive join F1, and nDCG@25 ≥ 0.93. The cost bar is easy
once you block the join; the quality bars are cliffs and they are where weak
plans die. The leaderboard ranks teams by cost among quality-passing plans.

## Report format

`report.pdf`, ≤ 4 pages (two columns fine), mirroring the lab page:

1. **Plan diagram** of your final E3 plan — every operator annotated with
   tier, thresholds (and the δ and m that produced them), and order.
2. **Cost/quality table** for E-NAIVE / E-CASCADE / E-CASCADE+REORDER:
   3 seeds × (USD, calls by tier, P1/P2/join F1, nDCG@25), mean ± range.
3. **Pareto sweep:** δ ∈ {0.20, 0.10, 0.05, 0.01} × `blocker_m` ∈ {4, 8, 16};
   cost (log x) vs join F1 (y); mark your submitted operating point.
4. **Ablation table:** E3 minus one mechanism at a time (no cascade, no
   reorder, no blocking, no cheap tier) — one row each, one paragraph each.
5. **"Where the guarantee leaks":** every cascade has a leak; name yours
   (hint: what distribution did you calibrate on, and what distribution does
   the blocked join actually route?).

Every number in the report must reproduce from your code: submit the repo
with your plan serialization, a `results/` directory, and a single entry
point (`python3 run_experiments.py`) that regenerates all of `results/`
byte-identically.

## How the accuracy budget is checked

The gold standard is *frozen by construction*: every label is a pure function
of `(course_salt, doc kind, doc id, aspect)` — see
`simulator.ground_truth_filter`. No labels file can drift, and the same
`grade_filter` / `grade_pairs` / `ndcg_at_k` functions you can call locally
are exactly what the autograder runs. The grader executes your submitted plan
on seeds 2027–2029 **plus one unpublished seed**, recomputes every quality
metric from gold, recomputes cost from its own ledger, and rejects any run
whose self-reported cost deviates by more than 0.5%.

Rules of engagement with gold:

- `calibration_reviews.csv` and `calibration_products.csv` are the **only**
  gold labels your code may consume. They are an i.i.d. sample by
  construction, which is precisely what makes your Hoeffding bound valid.
- The `Simulator.oracle_*` methods exist for the grader and for spot-checking
  your calibration code **on calibration ids only**. Every oracle call is
  recorded in the ledger (`Ledger.oracle_touches`); the grader re-runs your
  pipeline and rejects any submission whose operators touch the oracle.
- Yes, `simulator.py` is readable and the hidden rules are in it. Deriving
  labels from `ground_truth_filter`, the salt, or the planted signal
  fragments — in code or "by inspection" — is gold-standard mining, an
  academic-integrity violation, and detectable: the grader re-calibrates your
  thresholds on a shifted calibration split, and thresholds that did not come
  from your calibration code visibly fail to move with it.
- Determinism is graded: same seed + same plan ⇒ byte-identical output.
  Break ties with fixed lexicographic rules, never with simulator output from
  the benchmark seed (the seed-leakage pitfall on the lab page).

## FAQ

**Why does my all-frontier filter have precision < 1?** error_rate 0.02 at a
6% base rate means false positives are visible. That headroom is the entire
game: your optimized plan is measured *relative* to this imperfect naive
plan, not against perfection.

**Can my optimizer skip P2?** The pair predicate's truth already requires the
product to make the claim, so a plan that folds P2 into the join is a
legitimate optimizer discovery (predicate subsumption) — but P2 is still
conformance-tested in isolation for M1, so it must work.

**The naive join is 1.7M calls — do I really run it?** Yes, once per seed;
it's ~10 s of wall time. The 685 *simulated* hours of serial latency are the
point of the stretch metric.

**Where do plans live?** Serialize your `Plan` (see `Plan.describe()`) into
`results/` in any stable text format; the grader re-executes plans, it does
not trust prose.
