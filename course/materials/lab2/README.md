# Lab 2 starter skeleton — Mini-Neon (Go)

Starter code for **DATA 2027 Lab 2: Copy-on-Write Pages over Object Storage**.
This is the Go skeleton; the lab page is the authoritative spec for formats,
milestones, and grading. Read it first.

## Architecture recap

You are building the storage half of a Neon-style disaggregated Postgres,
stripped to its load-bearing walls:

```
 compute (not in this lab)            YOU BUILD THIS
┌───────────────┐   WAL    ┌────────────┐  immutable layer   ┌─────────────┐
│  safekeepers  │ ───────► │ pageserver │ ─────objects─────► │  S3 / MinIO │
│ (walgen plays │  stream  │            │ ◄────GET/range──── │   bucket    │
│  this role)   │          │ GetPage@LSN│                    └─────────────┘
└───────────────┘          └────────────┘
```

- **Safekeepers** durably accept the WAL stream and feed it to the pageserver
  in LSN order. In this lab, `cmd/walgen` plays that role: it emits a
  deterministic, seeded WAL stream in the normative wire format.
- **The pageserver** (you) ingests WAL into an in-memory *open layer*, seals
  it into immutable **delta layers**, periodically writes **image layers** to
  bound reads, and answers the one primitive that matters:
  `GetPage(branch, page, lsn)`.
- **S3/MinIO** holds all durable state: layer objects under
  `tl/<timeline>/...` and authoritative branch metadata under
  `branches/<name>.json`. Local disk is a cache only — the grader deletes it
  mid-run.

A **branch** is pure metadata: `{branch_id, parent_id, fork_lsn, ...}`. Reads
at `lsn <= fork_lsn` resolve through the ancestor chain; no page is ever
copied. That is the whole trick.

## Running MinIO

One liner (console at http://localhost:9001, login `minioadmin`/`minioadmin`):

```sh
docker run -d --name lab2-minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  quay.io/minio/minio server /data --console-address ":9001"
```

Connect from your code:

```go
store, err := lab2.NewMinIOStore(ctx, "localhost:9000",
    "minioadmin", "minioadmin", "lab2-myteam", false /* useSSL */)
```

`NewMinIOStore` creates the bucket if needed. For unit tests, use
`lab2.NewMemStore()` — same interface, plus `Gets`/`Puts` counters so you can
assert the layers-touched bound from Milestone 2 without a network.

To wipe state between runs: `docker rm -f lab2-minio` and re-run the one-liner.

## What is provided vs. what you write

| File                 | Status       | Contents |
|----------------------|--------------|----------|
| `types.go`           | **complete** | normative wire format: `WalRecord` encode/decode (crc32c), delta-span codec, `LayerFileMeta` + object-key grammar, `BranchMeta`. Do not change. |
| `storage.go`         | **complete** | `ObjectStore` (Put/Get/GetRange/List/Delete), in-memory fake with GET counters, MinIO implementation. |
| `cmd/walgen/main.go` | **complete** | deterministic seeded WAL generator; produces the 50-branch workload. Do not modify — seeds 1–9 are the grading seeds. |
| `pageserver.go`      | **TODO**     | M1/M2: `IngestWAL`, `Flush`, `GetPage` (the ancestry-walk algorithm is written out in comments), `materializePage`. |
| `branch.go`          | **TODO**     | M3/M4: `CreateBranch` (O(metadata)), `DeleteBranch`, two-phase `RunGC` with the safety invariant spelled out in comments. |

Build everything: `go build ./...` (run `go mod tidy` once to fetch minio-go).
The package compiles as-is; TODO bodies return "not implemented" errors so
you can wire up your test harness before writing a line of storage logic.

## The 50-branch demo workload

Generate the swarm workload (grading seed 1):

```sh
go run ./cmd/walgen -seed 1 -pages 1024 -records 20000 -branches 50 -out ./workload
```

This writes:

- `workload/main.wal` — 20,000 records for timeline `main` (first touch of a
  page is `FULL_PAGE`, later touches are `DELTA`),
- `workload/agent-001.wal` … `agent-050.wal` — 200 records per child branch,
  with LSNs strictly above the branch's fork point,
- `workload/plan.json` — the topology: each branch's `fork_lsn` (sampled from
  the middle 80% of main's history), its WAL file, and its final head LSN.

Your demo driver replays it as:

1. ingest `main.wal` into timeline `main`, then `Flush()`;
2. for each entry in `plan.json`: `CreateBranch(name, "main", fork_lsn)` —
   this must be one JSON PUT, no page I/O;
3. ingest each branch's `.wal` into its own timeline, flush;
4. issue `GetPage` reads on every branch, both below and above its
   `fork_lsn` (below-fork reads must be byte-identical to the parent at the
   same LSN);
5. `DeleteBranch` 10 of them, abandon 40, `RunGC(...)`, and verify the
   survivors still read correctly while bucket bytes shrink.

`walgen` is fully deterministic (its own SplitMix64 stream, not `math/rand`),
so identical flags produce identical bytes on every machine and Go version —
that is what makes the conformance vectors possible.

## Suggested order of attack

1. `materializePage` + a unit test against `MemStore` (an afternoon).
2. `IngestWAL`/`Flush` with a tiny manifest design; pass the crash test by
   killing and reconstructing the `Pageserver` in a test.
3. `GetPage` at head LSN only, then generalize to historical LSNs, then add
   image layers and the ancestry hop.
4. `CreateBranch` (it is ~30 lines if your resolution is right and a redesign
   if it is not).
5. GC last, with the swarm workload above as your integration test.
