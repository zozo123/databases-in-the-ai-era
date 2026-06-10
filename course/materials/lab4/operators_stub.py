"""
DATA 2027 · Lab 4 — semantic operators. THIS is the file you edit.

You implement three operators with statistical contracts, plus the plan and
cost machinery your optimizer needs to choose between candidate executions:

    sem_filter(sim, rows, predicate, target_precision=0.95, plan=None)
    sem_join(sim, left, right, predicate, budget=None, plan=None)
    sem_topk(sim, rows, criterion, k, plan=None)

"rows" / "left" / "right" are lists of dicts as produced by csv.DictReader
over reviews.csv / products.csv (call load_csv below). All model access goes
through the provided Simulator; the ledger it carries is the only meter the
grader trusts, so never instantiate a second Simulator mid-pipeline.

A NAIVE sem_filter is provided and working: frontier tier, every row, written
order. It is your M1 control arm and your correctness reference — your
cascaded version must match its quality contract, not its bill.

Run the smoke test (needs reviews.csv from make_data.py in the same dir):

    python3 operators_stub.py
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from simulator import Ledger, Simulator, grade_filter

Row = dict[str, Any]


# ---------------------------------------------------------------------------
# Plan and cost machinery
# ---------------------------------------------------------------------------

@dataclass
class CascadeConfig:
    """Routing thresholds for one predicate. Both taus MUST come out of your
    calibration code (calibrate_thresholds below) — hand-tuned constants are
    a rubric violation and fail the grader's shifted-split re-run."""
    tau_lo: float = 0.0          # score <= tau_lo  -> reject, no model call
    tau_hi: float = 1.0          # score >= tau_hi  -> accept, no model call
    delta: float = 0.05          # per-threshold confidence parameter
    escalate_tier: str = "cheap"     # middle band goes here first
    final_tier: str = "frontier"     # low-confidence residue goes here


@dataclass
class OpConfig:
    """Everything the optimizer decided about one operator instance."""
    op: str                              # "sem_filter" | "sem_join" | "sem_topk"
    predicate: str
    tier: str = "frontier"
    cascade: Optional[CascadeConfig] = None
    blocker_m: Optional[int] = None      # sem_join: keep top-m products / review
    pilot_n: int = 0                     # rows sampled for selectivity estimation


@dataclass
class Plan:
    """An executable plan: operator configs plus an execution order over the
    op names (e.g. ["P2", "P1", "JOIN", "TOPK"]). Serialize it into your
    report; the grader re-executes plans, it does not trust prose."""
    ops: dict[str, OpConfig] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = [f"order: {' -> '.join(self.order)}"]
        for name in self.order:
            c = self.ops[name]
            extra = ""
            if c.cascade:
                extra = (f"  cascade(tau_lo={c.cascade.tau_lo:.3f}, "
                         f"tau_hi={c.cascade.tau_hi:.3f}, delta={c.cascade.delta})")
            if c.blocker_m:
                extra += f"  blocker_m={c.blocker_m}"
            lines.append(f"  {name}: {c.op} tier={c.tier}{extra}")
        return "\n".join(lines)


@dataclass
class Cost:
    """Measured cost of an execution window. Build it from the ledger via
    Cost.measure() so every number flows through one choke point."""
    usd: float
    calls: int
    latency_ms: float
    by_tier: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def measure(cls, ledger: Ledger, start_index: int = 0) -> "Cost":
        entries = ledger.entries[start_index:]
        usd = sum(e.usd for e in entries)
        lat = sum(e.latency_ms for e in entries)
        by: dict[str, dict[str, float]] = {}
        for e in entries:
            t = by.setdefault(e.tier, {"calls": 0, "usd": 0.0})
            t["calls"] += 1
            t["usd"] += e.usd
        return cls(usd=usd, calls=len(entries), latency_ms=lat, by_tier=by)


# ---------------------------------------------------------------------------
# Calibration statistics (Milestone 2). Implement, do not eyeball.
# ---------------------------------------------------------------------------

def hoeffding_lower_bound(p_hat: float, m: int, delta: float) -> float:
    """One-sided lower confidence bound: with prob >= 1 - delta the true rate
    is at least p_hat - sqrt(ln(1/delta) / (2m)). Valid for any m >= 1."""
    if m < 1:
        return 0.0
    return max(0.0, p_hat - math.sqrt(math.log(1.0 / delta) / (2.0 * m)))


def calibrate_thresholds(scores_and_labels: Sequence[tuple[float, bool]],
                         target_precision: float, target_recall: float,
                         delta: float = 0.05) -> CascadeConfig:
    """Choose (tau_lo, tau_hi) from calibration pairs (proxy_score, gold).

    Contract (see lab page, Milestone 2):
      * tau_hi = the LOOSEST threshold whose accept-band precision lower
        bound (hoeffding_lower_bound, this delta) still clears
        target_precision. Loosest, because every extra accepted row is a
        model call you never pay for.
      * tau_lo = mirror image against target_recall (bound the fraction of
        gold positives that the reject band would throw away).
      * Sweep thresholds over the observed score grid; break ties by a fixed
        lexicographic rule, never by simulator output (seed-leakage pitfall).

    TODO(student): implement. The returned config must be reproducible from
    the calibration split alone.
    """
    raise NotImplementedError("Milestone 2: implement threshold calibration")


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

def sem_filter(sim: Simulator, rows: Sequence[Row], predicate: str,
               target_precision: float = 0.95,
               plan: Optional[OpConfig] = None) -> list[Row]:
    """Return the rows satisfying `predicate`.

    Contract: precision >= target_precision and recall >= 0.90 against the
    frozen gold standard (grade_filter). Output order must equal input order.

    NAIVE REFERENCE (working, M1): every row to the frontier tier. Your
    optimized version replaces the body with a calibrated cascade —
    score_proxy first, accept/reject outside the (tau_lo, tau_hi) band,
    escalate the middle to plan.cascade.escalate_tier, and send only
    low-confidence disagreements to plan.cascade.final_tier.
    """
    if plan is not None and plan.cascade is not None:
        # TODO(student): cascade path goes here (Milestone 2).
        raise NotImplementedError("Milestone 2: cascaded sem_filter")
    tier = plan.tier if plan is not None else "frontier"
    return [r for r in rows if sim.judge_filter(r, predicate, tier=tier)]


def sem_join(sim: Simulator, left: Sequence[Row], right: Sequence[Row],
             predicate: str, budget: Optional[float] = None,
             plan: Optional[OpConfig] = None) -> list[tuple[Any, Any]]:
    """Return (left_id, right_id) pairs satisfying `predicate`, sorted by
    (left_id, right_id) as integers, deduplicated.

    Contract: pair-level precision >= 0.95, recall >= 0.90 (grade_pairs).
    `budget`, if given, is a hard USD ceiling for THIS operator: check
    Cost.measure(sim.ledger, mark) as you go and stop escalating — never
    stop scoring — when you would exceed it.

    The naive version judges the full cross-product of surviving rows at the
    frontier tier; price it on a small slice before you ever run it for real
    (it is the $-quadratic blowup the lab page warns about). Your optimized
    version blocks with the proxy first: for each left row keep the top
    plan.blocker_m right rows by score_proxy, then cascade only those pairs.

    TODO(student): implement (naive for M1, blocked+cascaded for M3).
    """
    raise NotImplementedError("Milestone 1: sem_join")


def sem_topk(sim: Simulator, rows: Sequence[Row], criterion: str, k: int,
             plan: Optional[OpConfig] = None) -> list[Row]:
    """Return exactly k rows ranked best-first under `criterion`.

    Contract: nDCG@k >= 0.93 against the frozen latent scores (ndcg_at_k).
    Comparisons go through sim.judge_pair(a, b, criterion, tier=...), which
    answers "does a beat b?" — ties in gold break by ascending doc id, so a
    deterministic comparison sort is well-defined. Every comparison is
    billed: a full O(n log n) sort at the frontier tier is correct and
    ruinous. Consider a proxy-ordered shortlist followed by a tournament
    over the shortlist (shortlist size is a plan parameter; calibrate the
    recall it sacrifices like any other threshold).

    TODO(student): implement (naive for M1, shortlisted for M3).
    """
    raise NotImplementedError("Milestone 1: sem_topk")


# ---------------------------------------------------------------------------
# Data loading + smoke test
# ---------------------------------------------------------------------------

def load_csv(path: str) -> list[Row]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


if __name__ == "__main__":
    P1 = "complains about battery degradation"
    reviews = load_csv("reviews.csv")[:500]          # small slice: smoke test only
    sim = Simulator(seed=2027)

    mark = len(sim.ledger.entries)
    kept = sem_filter(sim, reviews, P1)              # naive reference path
    cost = Cost.measure(sim.ledger, mark)
    q = grade_filter(reviews, P1, [r["review_id"] for r in kept])

    print(f"naive sem_filter on {len(reviews)} reviews -> kept {len(kept)}")
    print(f"  quality  precision={q['precision']:.3f} recall={q['recall']:.3f} "
          f"f1={q['f1']:.3f}")
    print(f"  cost     ${cost.usd:.4f} over {cost.calls} calls "
          f"({cost.latency_ms / 1000:.0f}s serial latency)")
    print(f"  ledger\n{sim.ledger.summary()}")
