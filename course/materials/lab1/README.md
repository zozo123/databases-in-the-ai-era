# VLSM — Lab 1 starter skeleton

DATA 2027 · Lab 1: *VLSM — an LSM-Tree with Vector Segments*.
This is the starter crate referenced by the [lab handout](../../lab-1.html).
Read the handout first; this README only covers the code you were handed.

## Repo layout

```
lab1/
├── Cargo.toml          # deps: rand, serde, csv — and nothing ANN-shaped (see Ground Rules)
├── README.md           # you are here
└── src/
    ├── lib.rs          # COMPLETE  core types, Engine trait, EngineConfig/HnswParams, errors
    ├── memtable.rs     # COMPLETE  BTreeMap memtable + DocId→Vector side-table, brute-force knn
    ├── sstable.rs      # SPEC + WRITER complete; SstReader is todo!() — Milestone 1
    ├── workload.rs     # COMPLETE  seeded agent-trace generator + brute-force recall oracle
    └── bin/
        └── bench.rs    # COMPLETE  smoke harness; the seed of your Milestone 4 harness
```

## Build, test, run

```sh
cargo test                      # unit tests for everything marked COMPLETE
cargo run --release --bin bench # replay the synthetic trace against the memtable
```

`bench` should print `recall@10 1.0000` — the memtable answers knn by exact
brute force, so anything below 1.0 means the harness/oracle plumbing is
broken, not the index. It also writes a one-row `pareto.csv` so you can see
the submission schema produced by real code. Always benchmark `--release`;
debug-build numbers are not comparable and will not be graded.

The grading suite (`cargo test --features grading`) is distributed
separately at the end of Week 2 and runs against this same crate layout —
don't rename modules or public items.

## Provided vs. TODO

| Piece | Status | Where |
|---|---|---|
| `Key`/`Value`/`DocId`/`Vector`, `Engine` trait, config, errors | provided | `lib.rs` |
| Memtable (put/get/scan/brute-force knn, flush iterator) | provided | `memtable.rs` |
| On-disk format spec (header, key block, codes, PQ codebook, row-id map, HNSW CSR, footer) | provided, normative | `sstable.rs` doc comments |
| `SstWriter` (codec 0 = raw f32) | provided | `sstable.rs` |
| `crc32c` reference implementation | provided | `sstable.rs` |
| Workload generator + ground-truth oracle + `recall()` | provided, frozen | `workload.rs` |
| `SstReader::{open,get,scan,vector}` with full corruption checks | **you — Milestone 1** | `sstable.rs` stubs |
| i8 scalar quantizer (codec 1) writer+reader paths | **you — Milestone 1** | extend `sstable.rs` |
| HNSW build/search/serialize (CSR section) | **you — Milestone 2** | new module |
| `Db` (levels, flush, graph-merging compaction) implementing `Engine` | **you — Milestones 2–3** | new modules |
| Real bench harness: sweeps, sampled recall, Pareto CSV | **you — Milestone 4** | grow `bin/bench.rs` |

Two invariants the rest of the lab leans on — don't break them:

1. **Doc-id alignment.** `Engine::put` assigns doc ids sequentially in put
   order; the generator pre-assigns the same sequence in
   `Op::Upsert::doc_id`. Replay the trace in order, exactly once, and
   ground-truth ids match engine ids with no translation table.
2. **The oracle is f32 over live rows.** `workload::brute_force_knn` is the
   only recall ground truth the rubric accepts. Recall measured against
   quantized codes, or against a corpus containing dead versions, will fail
   the grading oracle check.

## Submission format

Submit a tarball (or tagged repo) containing:

```
your-team/
├── src/ ...            # builds with `cargo build --release`, clippy-clean
├── FORMAT.md           # your final on-disk format, written for a stranger
├── CHECKPOINTS.md      # answers to checkpoints 1.1–1.3
├── report.pdf          # ≤ 6 pages; every figure regenerable by `make report`
├── Makefile            # `make report` regenerates all figures from pareto.csv
└── pareto.csv          # the measured frontier — schema below
```

### `pareto.csv` schema

One row per benchmarked configuration (≥ 6 non-dominated rows for full
frontier credit). Header row required; column order as listed. This is
exactly the `ParetoRow` struct in `lib.rs` serialized by the `csv` crate —
use it rather than hand-formatting.

| column | type | meaning |
|---|---|---|
| `config_id` | string | unique name, e.g. `m16-ef64-rr` |
| `m` | int | HNSW max out-degree (layer 0 uses 2m) |
| `ef_search` | int | query-time beam width |
| `rerank` | bool | f32 re-ranking of top 4k candidates on/off |
| `codec` | string | `f32`, `i8`, or `pq` |
| `recall_at_10` | float | sampled vs. brute-force f32 oracle, live rows only |
| `p50_ms` | float | median knn latency, milliseconds |
| `p99_ms` | float | tail knn latency, milliseconds |
| `ops_per_sec` | float | sustained throughput over the whole replay |
| `mem_bytes` | int | engine-resident bytes (your accounting; explain it in the report) |
| `commit` | string | git hash the row was measured at |

All rows must come from seed **2027** at dim **768**, k = 10, on hardware
stated in the report.

## Ground rules (abridged — handout is authoritative)

- **No vector/ANN crates**: no `faiss`, `hnswlib`, `usearch`,
  `instant-distance`, `qdrant-*` — including FFI, including "just for the
  baseline".
- **No serde-derived on-disk formats.** The `.sst` bytes are written and
  parsed by your code against the spec in `sstable.rs`. Serde is for
  CSV/JSON reporting only.
- Allowed extra deps: `crossbeam`, `rayon`, `memmap2`, `crc32c`. Anything
  else: ask on the forum first.
- `SstReader` must return `Err(Corrupt)` — never panic, never UB — on
  arbitrary bytes. The grading corpus contains 200 mutated files.
