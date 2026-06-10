//! VLSM — an LSM-tree with vector segments. DATA 2027, Lab 1 starter.
//!
//! What is provided and complete:
//!   - core types, the [`Engine`] trait, config, errors (this file)
//!   - [`memtable`]: a BTreeMap memtable with a vector side-table
//!   - [`sstable`]: the on-disk format spec and a working `SstWriter`
//!   - [`workload`]: a deterministic synthetic agent-workload generator
//!     with a brute-force ground-truth oracle
//!
//! What you write (see the handout): the `SstReader`, the i8 quantizer,
//! HNSW build/search/serialize, graph-merging compaction, and the real
//! benchmark harness. `todo!()` marks every stub.

pub mod memtable;
pub mod sstable;
pub mod workload;

use serde::{Deserialize, Serialize};

/// Keys are arbitrary byte strings, ordered lexicographically.
pub type Key = Vec<u8>;
/// Values are opaque byte strings (tool results, JSON blobs, …).
pub type Value = Vec<u8>;
/// Stable identity of one *version* of a vectored row. Doc ids are assigned
/// in put order and are never reused; an upsert of an existing key gets a
/// fresh `DocId`, and the old one becomes dead. Recall is always measured
/// against the doc ids of the *latest live* versions.
pub type DocId = u64;

/// A dense embedding. The lab fixes cosine distance over L2-normalized
/// inputs, so distance is implemented as `1 − dot`.
#[derive(Clone, Debug, PartialEq)]
pub struct Vector(pub Vec<f32>);

impl Vector {
    pub fn dim(&self) -> usize {
        self.0.len()
    }

    pub fn dot(&self, other: &Vector) -> f32 {
        debug_assert_eq!(self.dim(), other.dim());
        self.0.iter().zip(&other.0).map(|(a, b)| a * b).sum()
    }

    pub fn l2_norm(&self) -> f32 {
        self.dot(self).sqrt()
    }

    /// Normalize in place. Zero vectors are left untouched.
    pub fn normalize(&mut self) {
        let n = self.l2_norm();
        if n > 0.0 {
            for x in &mut self.0 {
                *x /= n;
            }
        }
    }

    /// Cosine distance, assuming both sides are already L2-normalized.
    /// Range [0, 2]; smaller is closer.
    pub fn cosine_distance(&self, other: &Vector) -> f32 {
        1.0 - self.dot(other)
    }
}

#[derive(Debug)]
pub enum Error {
    Io(std::io::Error),
    /// On-disk bytes failed validation (bad magic, bad CRC, truncated
    /// section, unaligned offset…). Your reader must return this — never
    /// panic, never UB — on arbitrary input.
    Corrupt(String),
    DimMismatch { expected: usize, got: usize },
    /// `SstWriter::add` requires strictly increasing keys.
    UnsortedKey,
    InvalidConfig(String),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Io(e) => write!(f, "io error: {e}"),
            Error::Corrupt(msg) => write!(f, "corrupt file: {msg}"),
            Error::DimMismatch { expected, got } => {
                write!(f, "vector dimension mismatch: expected {expected}, got {got}")
            }
            Error::UnsortedKey => write!(f, "keys must be added in strictly increasing order"),
            Error::InvalidConfig(msg) => write!(f, "invalid config: {msg}"),
        }
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Error {
        Error::Io(e)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

/// HNSW knobs. `m` is the max out-degree on layers > 0 (layer 0 uses `2m`);
/// `ef_construction` is the beam width during insertion; `ef_search` is the
/// default beam width at query time (callers may override per query).
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct HnswParams {
    pub m: usize,
    pub ef_construction: usize,
    pub ef_search: usize,
}

impl Default for HnswParams {
    fn default() -> Self {
        HnswParams { m: 16, ef_construction: 128, ef_search: 64 }
    }
}

/// Engine-wide configuration. Defaults match the grading configuration in
/// the handout; the bench harness sweeps `hnsw` and may sweep `level_fanout`.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EngineConfig {
    /// Vector dimension. 768 for grading; tests use smaller dims.
    pub dim: usize,
    /// Flush the memtable to L0 once it holds roughly this many bytes.
    pub memtable_max_bytes: usize,
    /// Size ratio between adjacent levels (L_{i+1} = fanout × L_i).
    pub level_fanout: usize,
    /// Max L0 files before L0→L1 compaction is triggered.
    pub l0_max_files: usize,
    pub hnsw: HnswParams,
}

impl Default for EngineConfig {
    fn default() -> Self {
        EngineConfig {
            dim: 768,
            memtable_max_bytes: 64 << 20, // 64 MiB
            level_fanout: 10,
            l0_max_files: 4,
            hnsw: HnswParams::default(),
        }
    }
}

/// The surface every storage layer in this lab speaks. The provided
/// [`memtable::Memtable`] implements it (brute-force `knn`); your full `Db`
/// (memtable + levels of SSTables) must implement it too, so the bench
/// harness can drive either one.
pub trait Engine {
    /// Insert or overwrite `key`. If `vector` is given, the engine assigns
    /// and returns a fresh [`DocId`] for this version. Doc ids must be
    /// assigned in strict put order — the workload generator's ground truth
    /// depends on it (see `workload.rs`).
    fn put(&mut self, key: Key, value: Value, vector: Option<Vector>) -> Result<Option<DocId>>;

    /// Point lookup of the latest live version.
    fn get(&self, key: &[u8]) -> Result<Option<Value>>;

    /// Range scan over live keys in `[start, end)`, ascending key order,
    /// tombstones and shadowed versions filtered out.
    fn scan(&self, start: &[u8], end: &[u8]) -> Result<Vec<(Key, Value)>>;

    /// k nearest neighbors of `query` by cosine distance over *live*
    /// vectors, nearest first. `ef` is the beam width hint; brute-force
    /// implementations may ignore it.
    fn knn(&self, query: &Vector, k: usize, ef: usize) -> Result<Vec<(DocId, f32)>>;
}

/// One row of the Pareto CSV you submit (`pareto.csv`, schema in README.md).
/// Serialize with the `csv` crate so headers match the schema exactly.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ParetoRow {
    /// Free-form unique name for the configuration, e.g. "m16-ef64-rr".
    pub config_id: String,
    pub m: usize,
    pub ef_search: usize,
    /// Whether f32 re-ranking of the top 4k candidates was enabled.
    pub rerank: bool,
    /// "f32" | "i8" | "pq" — codec of the vector codes section.
    pub codec: String,
    pub recall_at_10: f64,
    pub p50_ms: f64,
    pub p99_ms: f64,
    /// Sustained throughput over the whole replay (all op types).
    pub ops_per_sec: f64,
    /// Resident memory attributable to the engine (your accounting).
    pub mem_bytes: u64,
    /// Git commit hash the numbers were produced at.
    pub commit: String,
}
