//! Deterministic synthetic agent workload. COMPLETE — do not modify for
//! graded runs (graders regenerate the trace from the seed and diff it).
//!
//! Op mix: **70% knn**, **20% upserts**, **10% scans**, after a warmup
//! burst of upserts. Knn queries are drawn from a fixed pool of query
//! templates with **Zipfian repeats** (agents re-ask hot questions);
//! upsert keys are Zipfian too (hot sessions rewrite the same docs).
//! Every knn op carries its exact brute-force f32 ground truth, computed
//! over the rows live *at that point in the trace* — replay ops in order,
//! exactly once, and engine doc ids line up with `Op::Upsert::doc_id`
//! (sequential assignment, see `Engine::put`).

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

use crate::{DocId, Key, Value, Vector};

pub const DEFAULT_SEED: u64 = 2027;

#[derive(Clone, Debug)]
pub struct WorkloadConfig {
    pub seed: u64,
    /// Ops in the main trace (warmup upserts are extra, emitted first).
    pub num_ops: usize,
    pub dim: usize,
    /// Distinct upsert keys; Zipfian repeats over this space turn into
    /// version churn (the thing your compaction has to survive).
    pub keyspace: usize,
    /// Size of the knn query-template pool.
    pub num_query_templates: usize,
    /// Zipf exponent for both key and query skew. 0.0 = uniform.
    pub zipf_s: f64,
    /// Neighbors per knn op (and length of each ground-truth list).
    pub k: usize,
    /// Keys per scan op.
    pub scan_span: usize,
    /// Upserts emitted before the mixed trace so early knns aren't empty.
    pub warmup_upserts: usize,
}

impl Default for WorkloadConfig {
    fn default() -> Self {
        WorkloadConfig {
            seed: DEFAULT_SEED,
            num_ops: 1_000,
            dim: 768,
            keyspace: 512,
            num_query_templates: 64,
            zipf_s: 0.99,
            k: 10,
            scan_span: 16,
            warmup_upserts: 256,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Op {
    Upsert { key: Key, value: Value, vector: Vector, doc_id: DocId },
    Knn { query: Vector, k: usize, truth: Vec<DocId> },
    Scan { start: Key, end: Key },
}

pub struct Workload {
    pub cfg: WorkloadConfig,
    pub ops: Vec<Op>,
}

/// Zipfian sampler over ranks `0..n` (rank 0 hottest), via a precomputed
/// CDF and binary search. Hand-rolled because `rand_distr` is not in the
/// allowed dependency set — and it's 12 lines.
pub struct Zipf {
    cdf: Vec<f64>,
}

impl Zipf {
    pub fn new(n: usize, s: f64) -> Zipf {
        assert!(n > 0);
        let mut cdf = Vec::with_capacity(n);
        let mut acc = 0.0;
        for i in 1..=n {
            acc += 1.0 / (i as f64).powf(s);
            cdf.push(acc);
        }
        for c in &mut cdf {
            *c /= acc;
        }
        Zipf { cdf }
    }

    pub fn sample(&self, rng: &mut StdRng) -> usize {
        let u: f64 = rng.gen(); // in [0, 1)
        self.cdf.partition_point(|&c| c < u)
    }
}

/// Standard normal via Box–Muller (again: no `rand_distr`).
fn gauss(rng: &mut StdRng) -> f32 {
    let u1: f64 = rng.gen::<f64>().max(1e-12);
    let u2: f64 = rng.gen();
    ((-2.0 * u1.ln()).sqrt() * (std::f64::consts::TAU * u2).cos()) as f32
}

fn random_unit_vector(rng: &mut StdRng, dim: usize) -> Vector {
    let mut v = Vector((0..dim).map(|_| gauss(rng)).collect());
    v.normalize();
    v
}

fn key_for(slot: usize) -> Key {
    // Fixed-width hex ⇒ lexicographic order == numeric order, so scans are
    // contiguous slot ranges.
    format!("doc:{slot:08x}").into_bytes()
}

fn make_upsert(
    slot: usize,
    dim: usize,
    live: &mut [Option<(DocId, Vector)>],
    next_doc_id: &mut DocId,
    rng: &mut StdRng,
) -> Op {
    let vector = random_unit_vector(rng, dim);
    let doc_id = *next_doc_id;
    *next_doc_id += 1;
    let value: Value = (0..rng.gen_range(64..=256)).map(|_| rng.gen::<u8>()).collect();
    live[slot] = Some((doc_id, vector.clone()));
    Op::Upsert { key: key_for(slot), value, vector, doc_id }
}

/// Exact f32 k-NN over the live set: the recall oracle. Nearest first,
/// ties broken by doc id. This is the *only* ground truth the rubric
/// accepts — never measure recall against quantized codes.
pub fn brute_force_knn(live: &[Option<(DocId, Vector)>], query: &Vector, k: usize) -> Vec<DocId> {
    let mut cands: Vec<(f32, DocId)> = live
        .iter()
        .flatten()
        .map(|(id, v)| (query.cosine_distance(v), *id))
        .collect();
    cands.sort_by(|a, b| {
        a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal).then(a.1.cmp(&b.1))
    });
    cands.truncate(k);
    cands.into_iter().map(|(_, id)| id).collect()
}

/// `|truth ∩ result| / |truth|`; 1.0 when truth is empty.
pub fn recall(truth: &[DocId], result: &[DocId]) -> f64 {
    if truth.is_empty() {
        return 1.0;
    }
    let hits = result.iter().filter(|id| truth.contains(id)).count();
    hits as f64 / truth.len() as f64
}

pub fn generate(cfg: &WorkloadConfig) -> Workload {
    let mut rng = StdRng::seed_from_u64(cfg.seed);
    let key_zipf = Zipf::new(cfg.keyspace, cfg.zipf_s);
    let query_zipf = Zipf::new(cfg.num_query_templates, cfg.zipf_s);
    let templates: Vec<Vector> = (0..cfg.num_query_templates)
        .map(|_| random_unit_vector(&mut rng, cfg.dim))
        .collect();

    let mut live: Vec<Option<(DocId, Vector)>> = vec![None; cfg.keyspace];
    let mut next_doc_id: DocId = 0;
    let mut ops = Vec::with_capacity(cfg.warmup_upserts + cfg.num_ops);

    for slot in 0..cfg.warmup_upserts.min(cfg.keyspace) {
        ops.push(make_upsert(slot, cfg.dim, &mut live, &mut next_doc_id, &mut rng));
    }

    for _ in 0..cfg.num_ops {
        let r: f64 = rng.gen();
        if r < 0.70 {
            // Zipfian repeat: identical hot queries recur, so result caching
            // and graph-entry locality are measurable effects.
            let query = templates[query_zipf.sample(&mut rng)].clone();
            let truth = brute_force_knn(&live, &query, cfg.k);
            ops.push(Op::Knn { query, k: cfg.k, truth });
        } else if r < 0.90 {
            let slot = key_zipf.sample(&mut rng);
            ops.push(make_upsert(slot, cfg.dim, &mut live, &mut next_doc_id, &mut rng));
        } else {
            let start = rng.gen_range(0..cfg.keyspace);
            let end = (start + cfg.scan_span).min(cfg.keyspace);
            ops.push(Op::Scan { start: key_for(start), end: key_for(end) });
        }
    }

    Workload { cfg: cfg.clone(), ops }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn small() -> WorkloadConfig {
        WorkloadConfig {
            num_ops: 400,
            dim: 16,
            keyspace: 64,
            num_query_templates: 16,
            warmup_upserts: 32,
            ..WorkloadConfig::default()
        }
    }

    #[test]
    fn deterministic_with_expected_mix_and_truth() {
        let (a, b) = (generate(&small()), generate(&small()));
        assert_eq!(a.ops, b.ops, "same seed must reproduce the same trace");

        let main = &a.ops[32..]; // skip warmup
        let knn = main.iter().filter(|o| matches!(o, Op::Knn { .. })).count() as f64;
        assert!((knn / main.len() as f64 - 0.70).abs() < 0.08);
        for op in main {
            if let Op::Knn { k, truth, .. } = op {
                assert!(!truth.is_empty() && truth.len() <= *k);
            }
        }
    }
}
