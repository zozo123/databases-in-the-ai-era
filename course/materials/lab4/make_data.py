"""
DATA 2027 · Lab 4 — deterministic corpus generator (CSV harness edition).

Run once:

    python3 make_data.py            # writes into the current directory
    python3 make_data.py --outdir data/

Produces, byte-identically on every machine:

    reviews.csv               20,000 rows: review_id, product_id, stars, text
    products.csv               5,000 rows: product_id, title, description, category
    calibration_reviews.csv      400 rows: review_id, gold_battery_complaint
    calibration_products.csv     100 rows: product_id, gold_battery_claim

Everything is keyed by stable_hash(course_salt, "gen", ..., doc_id) — never by
generation order — so any slice of the corpus regenerates identically. The
calibration files are the ONLY rows whose gold labels you may use in your
calibration code. The grader re-checks quality on a disjoint, shifted split.

═══════════════════════════════════════════════════════════════════════════
INSTRUCTOR EDITION — hidden label rules (do not paraphrase these in handouts)
═══════════════════════════════════════════════════════════════════════════
Gold is a pure function of ids, defined in simulator.ground_truth_filter:

  review r satisfies aspect a       iff  U(salt,"gold","review",r,a) < rate_a
        rates: battery 0.06 · screen 0.09 · shipping 0.12 · audio 0.07
  product p claims aspect a         iff  U(salt,"gold","product",p,a) < rate_a
        rates: battery 0.30 · waterproof 0.15 · lightweight 0.22
  pair (r, p) joins on aspect a     iff  complaint(r,a) AND claim(p,a) AND
        r.product_id == p   (all gold pairs live on the link structure)
  top-k latent score (criterion c)  =    0.45·U(salt,"gold-score",kind,id,a)
        + 1.0·[gold positive] + 0.04·(5 − stars)

where U(...) = simulator.unit_float(...) ∈ [0,1) and salt = "data2027-lab4-v1".

Text generation honours gold exactly: a signal fragment from
simulator.SIGNAL_FRAGMENTS[(kind, aspect)] is planted iff the gold rule fires.
Neutral distractor sentences (battery mentioned WITHOUT a complaint) are
planted in ~15% of gold-negative reviews — these are the rows a keyword
heuristic gets wrong. Stars are confounded with complaints on purpose:
complaining reviews draw from {1,1,2,2,3}, clean reviews from {3,4,4,5,5},
so stars alone is a weak (not useless, not sufficient) proxy.

Link structure: review.product_id = 1 + stable_hash(salt,"gen","link",rid) % 5000.
Calibration rows: the 400 smallest review_ids (resp. 100 smallest product_ids)
with U(salt,"gen","calib-pick",kind,id) < 0.06 — an i.i.d. sample by
construction, since gold is hash-keyed. The grader's shifted split uses the
same rule with a different pick salt.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import csv
import os

from simulator import (
    DISTRACTOR_FRAGMENTS,
    PRODUCT_CLAIM_RATES,
    REVIEW_ASPECT_RATES,
    SIGNAL_FRAGMENTS,
    ground_truth_filter,
    stable_hash,
    unit_float,
)

SALT = "data2027-lab4-v1"      # must match simulator._COURSE_SALT
N_REVIEWS = 20_000
N_PRODUCTS = 5_000
N_CALIB_REVIEWS = 400
N_CALIB_PRODUCTS = 100

# ---------------------------------------------------------------------------
# Template fragment banks (style only — they carry no label signal; the label
# signal is exclusively the planted SIGNAL_FRAGMENTS sentence).
# ---------------------------------------------------------------------------

OPENERS_LOW = [          # stars <= 3
    "Really wanted to like this one.",
    "Two months in and I have regrets.",
    "Not what I was hoping for.",
    "I gave it a fair shot, but no.",
    "Underwhelmed, and that's being polite.",
]
OPENERS_HIGH = [         # stars >= 4
    "Honestly a pleasant surprise.",
    "Solid purchase, would buy again.",
    "Does exactly what the listing promised.",
    "Three weeks of daily use and still happy.",
    "Better than I expected at this price.",
]
FILLERS = [
    "Setup took about five minutes.",
    "The instructions are translated oddly but usable.",
    "Customer support replied within a day when I asked a question.",
    "My partner uses it more than I do at this point.",
    "It replaced an older model I'd had for years.",
    "Build quality feels about right for the price.",
    "I compared three similar models before choosing this.",
    "The color is closer to grey than the photos suggest.",
]
CLOSERS = [
    "Make of that what you will.",
    "Hope this review helps someone decide.",
    "I'll update this review if anything changes.",
    "Buy accordingly.",
    "That's the honest summary.",
]

CATEGORIES = ["headphones", "smartwatch", "tablet", "e-reader",
              "speaker", "camera", "drone", "power-bank"]
TITLE_ADJ = ["Aero", "Pulse", "Nimbus", "Vertex", "Echo", "Lumen",
             "Drift", "Forge", "Atlas", "Quanta"]
TITLE_NOUN = ["Pro", "Mini", "Max", "Lite", "Ultra", "One", "X2", "Go"]
PRODUCT_BOILERPLATE = [
    "Designed in close collaboration with daily commuters.",
    "Ships with a two-year limited warranty.",
    "Pairs instantly with all major platforms.",
    "Built from recycled aluminium and matte polymer.",
    "Firmware updates arrive over the air every quarter.",
    "Backed by a 30-day no-questions return window.",
]

REVIEW_ASPECTS = list(REVIEW_ASPECT_RATES)      # battery, screen, shipping, audio
PRODUCT_CLAIMS = list(PRODUCT_CLAIM_RATES)      # battery, waterproof, lightweight


def _pick(bank: list[str], *key) -> str:
    return bank[stable_hash(SALT, "gen", *key) % len(bank)]


# ---------------------------------------------------------------------------
# Row builders — pure functions of the doc id.
# ---------------------------------------------------------------------------

def build_review(rid: int) -> dict:
    complaints = [a for a in REVIEW_ASPECTS if ground_truth_filter("review", str(rid), a)]

    if complaints:
        stars = [1, 1, 2, 2, 3][stable_hash(SALT, "gen", "stars", rid) % 5]
    else:
        stars = [3, 4, 4, 5, 5][stable_hash(SALT, "gen", "stars", rid) % 5]

    parts = [_pick(OPENERS_LOW if stars <= 3 else OPENERS_HIGH, "open", rid)]
    for a in complaints:
        frag = _pick(SIGNAL_FRAGMENTS[("review", a)], "sig", rid, a)
        parts.append(frag[0].upper() + frag[1:] + ".")
    for a in REVIEW_ASPECTS:                      # neutral mentions, gold-negative only
        if a not in complaints and unit_float(SALT, "gen", "distract", rid, a) < 0.15:
            frag = _pick(DISTRACTOR_FRAGMENTS[("review", a)], "dis", rid, a)
            parts.append(frag[0].upper() + frag[1:] + ".")
    n_fill = 1 + stable_hash(SALT, "gen", "nfill", rid) % 3
    fill_start = stable_hash(SALT, "gen", "fill", rid) % len(FILLERS)
    for j in range(n_fill):                       # consecutive picks: no repeats
        parts.append(FILLERS[(fill_start + j) % len(FILLERS)])
    parts.append(_pick(CLOSERS, "close", rid))

    return {
        "review_id": rid,
        "product_id": 1 + stable_hash(SALT, "gen", "link", rid) % N_PRODUCTS,
        "stars": stars,
        "text": " ".join(parts),
    }


def build_product(pid: int) -> dict:
    claims = [a for a in PRODUCT_CLAIMS if ground_truth_filter("product", str(pid), a)]
    category = CATEGORIES[stable_hash(SALT, "gen", "cat", pid) % len(CATEGORIES)]
    title = (f"{_pick(TITLE_ADJ, 'tadj', pid)} "
             f"{_pick(TITLE_NOUN, 'tnoun', pid)} {category.title()} "
             f"{100 + stable_hash(SALT, 'gen', 'tnum', pid) % 900}")

    parts = [f"The {title} is a {category} for people who notice details."]
    for a in claims:
        frag = _pick(SIGNAL_FRAGMENTS[("product", a)], "psig", pid, a)
        parts.append(frag[0].upper() + frag[1:] + ".")
    n_boil = 1 + stable_hash(SALT, "gen", "nboil", pid) % 2
    for j in range(n_boil):
        parts.append(_pick(PRODUCT_BOILERPLATE, "boil", pid, j))

    return {
        "product_id": pid,
        "title": title,
        "description": " ".join(parts),
        "category": category,
    }


def calibration_ids(kind: str, universe: int, n: int) -> list[int]:
    picked = [i for i in range(1, universe + 1)
              if unit_float(SALT, "gen", "calib-pick", kind, i) < 0.06]
    return picked[:n]


# ---------------------------------------------------------------------------

def write_csv(path: str, fieldnames: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--outdir", default=".", help="output directory (default: cwd)")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    reviews = [build_review(r) for r in range(1, N_REVIEWS + 1)]
    products = [build_product(p) for p in range(1, N_PRODUCTS + 1)]

    write_csv(os.path.join(args.outdir, "reviews.csv"),
              ["review_id", "product_id", "stars", "text"], reviews)
    write_csv(os.path.join(args.outdir, "products.csv"),
              ["product_id", "title", "description", "category"], products)

    calib_r = [{"review_id": i,
                "gold_battery_complaint": int(ground_truth_filter("review", str(i), "battery"))}
               for i in calibration_ids("review", N_REVIEWS, N_CALIB_REVIEWS)]
    calib_p = [{"product_id": i,
                "gold_battery_claim": int(ground_truth_filter("product", str(i), "battery"))}
               for i in calibration_ids("product", N_PRODUCTS, N_CALIB_PRODUCTS)]
    write_csv(os.path.join(args.outdir, "calibration_reviews.csv"),
              ["review_id", "gold_battery_complaint"], calib_r)
    write_csv(os.path.join(args.outdir, "calibration_products.csv"),
              ["product_id", "gold_battery_claim"], calib_p)

    # sanity report
    n_batt = sum(ground_truth_filter("review", str(r["review_id"]), "battery")
                 for r in reviews)
    n_claim = sum(ground_truth_filter("product", str(p["product_id"]), "battery")
                  for p in products)
    avg_len = sum(len(r["text"]) for r in reviews) / len(reviews)
    print(f"reviews.csv              {len(reviews):>6} rows "
          f"(battery complaints: {n_batt}, {n_batt / len(reviews):.1%}; "
          f"avg text {avg_len:.0f} chars)")
    print(f"products.csv             {len(products):>6} rows "
          f"(battery-life claims: {n_claim}, {n_claim / len(products):.1%})")
    print(f"calibration_reviews.csv  {len(calib_r):>6} rows "
          f"(positives: {sum(c['gold_battery_complaint'] for c in calib_r)})")
    print(f"calibration_products.csv {len(calib_p):>6} rows "
          f"(positives: {sum(c['gold_battery_claim'] for c in calib_p)})")


if __name__ == "__main__":
    main()
