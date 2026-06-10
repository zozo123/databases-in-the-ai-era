"""
DATA 2027 · Lab 4 — llmsim, the deterministic mock-LLM simulator (CSV harness edition).

Treat this module as a sealed service: import it, call it, do not modify it.
Every judgement is a pure function of (course salt, seed, tier, call key), so
given the same seed and the same plan your pipeline produces byte-identical
output. That property is what makes the lab gradable; it is also a rubric row.

Public surface
--------------
ModelTier                       frozen dataclass: pricing, latency, error rate
TIERS                           presets: "proxy" (0.25), "cheap" (0.08), "frontier" (0.02)
Ledger / LedgerEntry            every billable call appends here
Simulator(seed)
    .judge_filter(doc, predicate, tier="frontier") -> bool      (billed)
    .judge_pair(left, right, predicate, tier="frontier") -> bool (billed)
    .score_proxy(text_or_doc, predicate) -> float in [0, 1]      ($0, billed at 0)
    .oracle_filter(doc, predicate) -> bool          GOLD — calibration rows only
    .oracle_pair(left, right, predicate) -> bool    GOLD — calibration rows only
    .oracle_score(doc, criterion) -> float          GOLD — calibration rows only
grade_filter / grade_pairs / ndcg_at_k              frozen-gold quality checks
stable_hash, unit_float                             shared with make_data.py

Docs are plain dicts straight out of csv.DictReader: a review has at least
{"review_id": ..., "text": ..., "stars": ..., "product_id": ...} and a product
{"product_id": ..., "title": ..., "description": ...}. Ground truth is keyed by
the doc id, never by re-reading the text, so re-wrapping rows cannot move gold.

Academic integrity: the oracle_* methods exist so the grader (and your
calibration code, on the ids listed in calibration_*.csv) can check quality.
Calling them on non-calibration rows from inside an operator is gold-standard
mining and is detected: the ledger records every oracle touch.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any, Iterable, Mapping, Sequence, Union

_COURSE_SALT = "data2027-lab4-v1"
_NORMAL = NormalDist()

Doc = Mapping[str, Any]


# --------------------------------------------------------------------------
# Deterministic hashing — the only randomness source in the whole harness.
# --------------------------------------------------------------------------

def stable_hash(*parts: Any) -> int:
    """64-bit hash, stable across processes and Python versions."""
    payload = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def unit_float(*parts: Any) -> float:
    """Uniform float in [0, 1) derived from stable_hash."""
    return stable_hash(*parts) / 2.0 ** 64


def _gauss(*parts: Any) -> float:
    u = min(max(unit_float(*parts), 1e-12), 1.0 - 1e-12)
    return _NORMAL.inv_cdf(u)


# --------------------------------------------------------------------------
# Model tiers
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelTier:
    name: str
    usd_per_1k_in: float
    usd_per_1k_out: float
    latency_ms: float
    error_rate: float


TIERS: dict[str, ModelTier] = {
    "proxy":    ModelTier("proxy",    0.000, 0.000,    0.4, 0.25),
    "cheap":    ModelTier("cheap",    0.025, 0.100,  180.0, 0.08),
    "frontier": ModelTier("frontier", 0.750, 3.000, 1400.0, 0.02),
}


# --------------------------------------------------------------------------
# Cost / latency ledger — one accounting choke point, as the lab demands.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LedgerEntry:
    op: str            # "judge_filter" | "judge_pair" | "score_proxy" | "oracle_*"
    tier: str
    tokens_in: int
    tokens_out: int
    usd: float
    latency_ms: float


@dataclass
class Ledger:
    entries: list[LedgerEntry] = field(default_factory=list)

    def add(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    @property
    def total_usd(self) -> float:
        return sum(e.usd for e in self.entries)

    @property
    def total_calls(self) -> int:
        return len(self.entries)

    @property
    def total_latency_ms(self) -> float:
        return sum(e.latency_ms for e in self.entries)

    def by_tier(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for e in self.entries:
            t = out.setdefault(e.tier, {"calls": 0, "usd": 0.0, "latency_ms": 0.0})
            t["calls"] += 1
            t["usd"] += e.usd
            t["latency_ms"] += e.latency_ms
        return out

    def oracle_touches(self) -> int:
        return sum(1 for e in self.entries if e.op.startswith("oracle"))

    def reset(self) -> None:
        self.entries.clear()

    def summary(self) -> str:
        rows = [f"  {tier:9s} calls={v['calls']:>9.0f}  usd=${v['usd']:>10.4f}  "
                f"latency={v['latency_ms'] / 1000:>9.1f}s"
                for tier, v in sorted(self.by_tier().items())]
        rows.append(f"  {'TOTAL':9s} calls={self.total_calls:>9d}  "
                    f"usd=${self.total_usd:>10.4f}  "
                    f"latency={self.total_latency_ms / 1000:>9.1f}s")
        return "\n".join(rows)


# --------------------------------------------------------------------------
# Predicate canonicalization. A predicate is free text; the simulator maps it
# onto one of the known aspects by keyword. Unknown predicates get a stable
# pseudo-aspect with 10% base selectivity, so nothing ever crashes.
# --------------------------------------------------------------------------

# aspect -> base selectivity of the hidden gold rule, per doc kind
REVIEW_ASPECT_RATES = {"battery": 0.06, "screen": 0.09, "shipping": 0.12, "audio": 0.07}
PRODUCT_CLAIM_RATES = {"battery": 0.30, "waterproof": 0.15, "lightweight": 0.22}

_ASPECT_KEYWORDS = ["battery", "screen", "shipping", "audio", "sound",
                    "waterproof", "water", "lightweight", "weight"]
_ASPECT_ALIASES = {"sound": "audio", "water": "waterproof", "weight": "lightweight"}

_COMPARE_MARKERS = ("most ", "more ", "credib", "better", "stronger", "rank", "best ")


def canonical_aspect(predicate: str) -> str:
    p = predicate.lower()
    for kw in _ASPECT_KEYWORDS:
        if kw in p:
            return _ASPECT_ALIASES.get(kw, kw)
    return f"pseudo-{stable_hash(_COURSE_SALT, 'aspect', p.strip()) % 997}"


def is_comparison(predicate: str) -> bool:
    """True if the predicate reads like a ranking criterion (used by sem_topk)."""
    p = predicate.lower()
    return any(m in p for m in _COMPARE_MARKERS)


def _doc_key(doc: Doc) -> tuple[str, str]:
    if "review_id" in doc:
        return "review", str(doc["review_id"])
    if "product_id" in doc:
        return "product", str(doc["product_id"])
    raise KeyError("doc needs a 'review_id' or 'product_id' field")


def _doc_text(doc: Union[Doc, str]) -> str:
    if isinstance(doc, str):
        return doc
    return str(doc.get("text") or doc.get("description") or doc.get("title") or "")


# --------------------------------------------------------------------------
# Signal fragments. make_data.py plants exactly one fragment from the matching
# bank iff the hidden gold rule fires, so text and gold can never disagree.
# Shared here (not in make_data.py) so the raw-string truth path below works.
# --------------------------------------------------------------------------

SIGNAL_FRAGMENTS: dict[tuple[str, str], list[str]] = {
    ("review", "battery"): [
        "the battery barely lasts an hour now, down from a full day",
        "battery health dropped off a cliff after two months of light use",
        "it dies before lunch — the battery has degraded badly since I bought it",
    ],
    ("review", "screen"): [
        "the screen developed dead pixels within weeks",
        "there is permanent ghosting on the screen already",
        "the display flickers constantly at low brightness",
    ],
    ("review", "shipping"): [
        "shipping took three weeks and the box arrived crushed",
        "it shipped late and the courier left it in the rain",
        "the package showed up damaged after a long shipping delay",
    ],
    ("review", "audio"): [
        "the audio crackles at any volume above half",
        "one speaker channel cut out entirely after a month",
        "constant static hiss from the speakers ruins the sound",
    ],
    ("product", "battery"): [
        "engineered for all-day battery life on a single charge",
        "our long-life cell delivers up to 48 hours of battery",
        "marathon battery life that outlasts your longest day",
    ],
    ("product", "waterproof"): [
        "fully waterproof to IP68 for worry-free use in the rain",
        "a waterproof seal rated for submersion up to two meters",
    ],
    ("product", "lightweight"): [
        "an ultra-lightweight shell at just a few hundred grams",
        "featherweight construction you will forget you are carrying",
    ],
}

# Neutral distractors: mention the aspect WITHOUT satisfying the predicate.
# These are what make a naive keyword filter (and a loose proxy) overfire.
DISTRACTOR_FRAGMENTS: dict[tuple[str, str], list[str]] = {
    ("review", "battery"): [
        "battery life is exactly as advertised, no complaints there",
        "I charge the battery overnight out of habit and it is always fine",
    ],
    ("review", "screen"): [
        "the screen looks crisp and bright even outdoors",
    ],
    ("review", "shipping"): [
        "shipping was uneventful and the box arrived intact",
    ],
    ("review", "audio"): [
        "the audio is balanced and plenty loud for a small room",
    ],
}


# --------------------------------------------------------------------------
# Hidden ground truth. Pure function of (course salt, doc kind, doc id,
# aspect): the gold standard is frozen by construction and identical for
# every student and every seed. make_data.py imports ground_truth_filter so
# the planted text fragments always agree with gold.
# --------------------------------------------------------------------------

def ground_truth_filter(kind: str, doc_id: str, aspect: str) -> bool:
    if kind == "review":
        rate = REVIEW_ASPECT_RATES.get(aspect, 0.10)
    else:
        rate = PRODUCT_CLAIM_RATES.get(aspect, 0.10)
    return unit_float(_COURSE_SALT, "gold", kind, str(doc_id), aspect) < rate


def _truth_filter_doc(doc: Union[Doc, str], predicate: str) -> bool:
    aspect = canonical_aspect(predicate)
    if isinstance(doc, str):
        # Raw-string path: detect the planted signal fragment. make_data.py
        # plants a fragment iff ground_truth_filter() is true, so this agrees
        # with the id-keyed rule on generated rows.
        text = doc.lower()
        frags = SIGNAL_FRAGMENTS.get(("review", aspect), []) + \
                SIGNAL_FRAGMENTS.get(("product", aspect), [])
        return any(f.lower() in text for f in frags)
    kind, doc_id = _doc_key(doc)
    return ground_truth_filter(kind, doc_id, aspect)


def _truth_pair(left: Doc, right: Doc, predicate: str) -> bool:
    """Join truth: the review complains about the aspect, the product claims
    it, and the review is about exactly that product (the linked structure —
    a review can only "discuss this product's claim" for its own product)."""
    aspect = canonical_aspect(predicate)
    lkind, lid = _doc_key(left)
    rkind, rid = _doc_key(right)
    if lkind == rkind:  # degenerate self-join: pure hash rule, 2% selectivity
        a, b = sorted([f"{lkind}:{lid}", f"{rkind}:{rid}"])
        return unit_float(_COURSE_SALT, "gold-pair", a, b, aspect) < 0.02
    rev, prod = (left, right) if lkind == "review" else (right, left)
    rev_id, prod_id = _doc_key(rev)[1], _doc_key(prod)[1]
    if str(rev.get("product_id", "")) != str(prod_id):
        return False
    return (ground_truth_filter("review", rev_id, aspect)
            and ground_truth_filter("product", prod_id, aspect))


def _truth_score(doc: Doc, criterion: str) -> float:
    """Latent credibility score for ranking criteria (sem_topk gold)."""
    aspect = canonical_aspect(criterion)
    kind, doc_id = _doc_key(doc)
    base = unit_float(_COURSE_SALT, "gold-score", kind, doc_id, aspect)
    score = 0.45 * base
    if ground_truth_filter(kind, doc_id, aspect):
        score += 1.0                                  # true positives dominate
    try:
        stars = int(doc.get("stars", 3))
    except (TypeError, ValueError):
        stars = 3
    score += 0.04 * (5 - stars)                       # angrier == more credible
    return score


# --------------------------------------------------------------------------
# The simulator
# --------------------------------------------------------------------------

class Simulator:
    """Deterministic mock LLM. seed changes the *errors*, never the gold."""

    def __init__(self, seed: int = 2027, proxy_error: float | None = None,
                 ledger: Ledger | None = None):
        self.seed = int(seed)
        self.tiers = dict(TIERS)
        self.ledger = ledger if ledger is not None else Ledger()
        err = TIERS["proxy"].error_rate if proxy_error is None else float(proxy_error)
        if not 0.0 < err < 0.5:
            raise ValueError("proxy_error must be in (0, 0.5)")
        self.proxy_error = err
        # sigma such that P(score crosses 0.5 against gold) == proxy_error
        self._proxy_sigma = 0.5 / _NORMAL.inv_cdf(1.0 - err)

    # -- billing -----------------------------------------------------------

    @staticmethod
    def _tokens_in(predicate: str, *texts: str) -> int:
        return max(1, (len(predicate) + sum(len(t) for t in texts)) // 4 + 16)

    def _bill(self, op: str, tier: ModelTier, tokens_in: int, tokens_out: int) -> None:
        usd = tokens_in / 1000.0 * tier.usd_per_1k_in \
            + tokens_out / 1000.0 * tier.usd_per_1k_out
        self.ledger.add(LedgerEntry(op, tier.name, tokens_in, tokens_out,
                                    usd, tier.latency_ms))

    def _tier(self, tier: Union[str, ModelTier]) -> ModelTier:
        if isinstance(tier, ModelTier):
            return tier
        try:
            return self.tiers[tier]
        except KeyError:
            raise KeyError(f"unknown tier {tier!r}; have {sorted(self.tiers)}") from None

    # -- noisy flip shared by both judges ----------------------------------
    #
    # error_rate is the headline miss rate (P[flip | gold positive]). Flips
    # are class-conditional, the way real judge models err:
    #   filter:  cheap is yes-biased (negatives flip at 1.5x, positives at
    #            0.25x); other tiers hallucinate positives at error_rate/10.
    #   pair:    matchers are conservative — errors are missed matches;
    #            false matches occur only on the cheap tier (error_rate/4).
    #   compare: a flip is just a swapped comparison, symmetric at error_rate.
    # Sanity math at the benchmark base rates: all-frontier sem_filter on P1
    # lands near precision 0.97 / recall 0.98, so the M1 contract
    # (precision >= 0.95, recall >= 0.90) is satisfiable — and tight enough
    # that a sloppy cascade visibly breaks it.

    def _flip(self, truth: bool, tier: ModelTier, kind: str, *key: Any) -> bool:
        e = tier.error_rate
        if kind == "compare":
            p = e
        elif kind == "pair":
            p = e if truth else (e * 0.25 if tier.name == "cheap" else 0.0)
        else:  # "filter"
            if tier.name == "cheap":
                p = e * 0.25 if truth else e * 1.5
            else:
                p = e if truth else e * 0.1
        flipped = unit_float(_COURSE_SALT, "flip", self.seed, tier.name, kind, *key) < p
        return truth != flipped

    # -- billed judgements --------------------------------------------------

    def judge_filter(self, doc: Union[Doc, str], predicate: str,
                     tier: Union[str, ModelTier] = "frontier") -> bool:
        t = self._tier(tier)
        truth = _truth_filter_doc(doc, predicate)
        key = doc if isinstance(doc, str) else ":".join(_doc_key(doc))
        self._bill("judge_filter", t, self._tokens_in(predicate, _doc_text(doc)), 2)
        return self._flip(truth, t, "filter", key, predicate.strip().lower())

    # (kind "filter" is also part of the hash key above, so filter, pair and
    # comparison judgements never share error coins.)

    def judge_pair(self, left: Doc, right: Doc, predicate: str,
                   tier: Union[str, ModelTier] = "frontier") -> bool:
        """Boolean pair judgement. Join predicates ask 'do these two match?';
        ranking criteria (is_comparison(predicate) == True) ask 'does left
        beat right?' — ties broken by ascending doc id, so a comparison sort
        built on judge_pair stays deterministic."""
        t = self._tier(tier)
        lkey, rkey = ":".join(_doc_key(left)), ":".join(_doc_key(right))
        if is_comparison(predicate):
            ls, rs = _truth_score(left, predicate), _truth_score(right, predicate)
            truth = (ls, rkey) > (rs, lkey)
            kind = "compare"
        else:
            truth = _truth_pair(left, right, predicate)
            kind = "pair"
        self._bill("judge_pair", t,
                   self._tokens_in(predicate, _doc_text(left), _doc_text(right)), 2)
        return self._flip(truth, t, kind, lkey, rkey, predicate.strip().lower())

    # -- free proxy ----------------------------------------------------------

    def score_proxy(self, text: Union[Doc, str], predicate: str) -> float:
        """Embedding-similarity-style score in [0, 1]. Costs $0 (the call is
        still ledgered at zero dollars). Thresholding at 0.5 disagrees with
        gold at rate `proxy_error` (default 0.25), and extreme scores are
        genuinely more reliable — that monotone behaviour is what makes
        cascade calibration work. Accepts a doc dict or the raw text string."""
        truth = _truth_filter_doc(text, predicate)
        key = text if isinstance(text, str) else ":".join(_doc_key(text))
        g = _gauss(_COURSE_SALT, "proxy", self.seed, key, predicate.strip().lower())
        raw = (1.0 if truth else 0.0) + self._proxy_sigma * g
        score = 0.25 + 0.5 * raw
        t = self.tiers["proxy"]
        self._bill("score_proxy", t, self._tokens_in(predicate, _doc_text(text)), 0)
        return min(1.0, max(0.0, score))

    # -- gold oracle (calibration rows + grader only) ------------------------

    def oracle_filter(self, doc: Union[Doc, str], predicate: str) -> bool:
        self.ledger.add(LedgerEntry("oracle_filter", "gold", 0, 0, 0.0, 0.0))
        return _truth_filter_doc(doc, predicate)

    def oracle_pair(self, left: Doc, right: Doc, predicate: str) -> bool:
        self.ledger.add(LedgerEntry("oracle_pair", "gold", 0, 0, 0.0, 0.0))
        if is_comparison(predicate):
            lk, rk = ":".join(_doc_key(left)), ":".join(_doc_key(right))
            return (_truth_score(left, predicate), rk) > (_truth_score(right, predicate), lk)
        return _truth_pair(left, right, predicate)

    def oracle_score(self, doc: Doc, criterion: str) -> float:
        self.ledger.add(LedgerEntry("oracle_score", "gold", 0, 0, 0.0, 0.0))
        return _truth_score(doc, criterion)


# --------------------------------------------------------------------------
# Frozen-gold quality checks — the same functions the autograder runs.
# --------------------------------------------------------------------------

def grade_filter(docs: Sequence[Doc], predicate: str,
                 predicted_ids: Iterable[Any]) -> dict[str, float]:
    """Precision / recall / F1 of a sem_filter output against frozen gold.
    `predicted_ids` are the id values (review_id or product_id) you kept."""
    pred = {str(i) for i in predicted_ids}
    tp = fp = fn = 0
    for d in docs:
        _, doc_id = _doc_key(d)
        truth = _truth_filter_doc(d, predicate)
        if doc_id in pred:
            tp += truth
            fp += not truth
        elif truth:
            fn += 1
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": prec, "recall": rec, "f1": f1,
            "tp": tp, "fp": fp, "fn": fn}


def grade_pairs(left_docs: Sequence[Doc], right_docs: Sequence[Doc], predicate: str,
                predicted_pairs: Iterable[tuple[Any, Any]]) -> dict[str, float]:
    """Pair-level P/R/F1 for sem_join. Gold pairs all live on the linked
    (review.product_id == product.product_id) structure, so the exact gold
    set enumerates in O(n) instead of O(n^2)."""
    right_by_id = {str(_doc_key(r)[1]): r for r in right_docs}
    gold: set[tuple[str, str]] = set()
    for l in left_docs:
        rid = str(l.get("product_id", ""))
        r = right_by_id.get(rid)
        if r is not None and _truth_pair(l, r, predicate):
            gold.add((str(_doc_key(l)[1]), rid))
    pred = {(str(a), str(b)) for a, b in predicted_pairs}
    tp = len(pred & gold)
    prec = tp / len(pred) if pred else 0.0
    rec = tp / len(gold) if gold else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": prec, "recall": rec, "f1": f1,
            "predicted": len(pred), "gold": len(gold)}


def ndcg_at_k(ranked_docs: Sequence[Doc], universe: Sequence[Doc],
              criterion: str, k: int) -> float:
    """nDCG@k of a sem_topk ranking against the frozen latent scores."""
    gains = {":".join(_doc_key(d)): _truth_score(d, criterion) for d in universe}
    ideal = sorted(gains.values(), reverse=True)[:k]
    dcg = sum(gains.get(":".join(_doc_key(d)), 0.0) / math.log2(i + 2)
              for i, d in enumerate(ranked_docs[:k]))
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


if __name__ == "__main__":
    sim = Simulator(seed=2027)
    doc = {"review_id": 32, "product_id": 4242, "stars": 2,
           "text": "the battery barely lasts an hour now, down from a full day"}
    print("judge_filter (frontier):",
          sim.judge_filter(doc, "complains about battery degradation"))
    print("score_proxy           :",
          round(sim.score_proxy(doc, "complains about battery degradation"), 4))
    print("ledger:\n" + sim.ledger.summary())
