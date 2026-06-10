//! Smoke bench: replays the synthetic agent trace against the memtable
//! alone (brute-force knn ⇒ recall should print 1.0000 — that's the
//! end-to-end sanity check of the harness + oracle plumbing).
//!
//! This is the seed of your Milestone 4 harness, not the harness itself:
//! swap `Memtable` for your `Db`, sweep `HnswParams`, replay at a fixed
//! arrival rate, and emit one `ParetoRow` per configuration.
//!
//! Run: `cargo run --release --bin bench`

use std::time::Instant;

use vlsm::memtable::Memtable;
use vlsm::workload::{self, Op, WorkloadConfig};
use vlsm::{Engine, ParetoRow};

fn main() {
    // Small dim so a debug build still finishes; grading uses dim = 768.
    let cfg = WorkloadConfig { dim: 128, ..WorkloadConfig::default() };
    eprintln!(
        "generating trace: seed={} ops={} dim={} k={}",
        cfg.seed, cfg.num_ops, cfg.dim, cfg.k
    );
    let wl = workload::generate(&cfg);

    let mut mt = Memtable::new(cfg.dim);
    let mut knn_ms: Vec<f64> = Vec::new();
    let mut recalls: Vec<f64> = Vec::new();
    let wall = Instant::now();

    for op in &wl.ops {
        match op {
            Op::Upsert { key, value, vector, doc_id } => {
                let got = mt.put(key.clone(), value.clone(), Some(vector.clone())).expect("put");
                assert_eq!(got, Some(*doc_id), "doc-id drift: replay the trace in order, once");
            }
            Op::Knn { query, k, truth } => {
                let t = Instant::now();
                let res = mt.knn(query, *k, 64).expect("knn");
                knn_ms.push(t.elapsed().as_secs_f64() * 1e3);
                let ids: Vec<_> = res.iter().map(|&(id, _)| id).collect();
                recalls.push(workload::recall(truth, &ids));
            }
            Op::Scan { start, end } => {
                mt.scan(start, end).expect("scan");
            }
        }
    }

    let elapsed = wall.elapsed().as_secs_f64();
    assert!(!knn_ms.is_empty(), "trace contained no knn ops?");
    knn_ms.sort_by(|a, b| a.partial_cmp(b).expect("finite latencies"));
    let pct = |p: f64| knn_ms[((knn_ms.len() - 1) as f64 * p) as usize];
    let mean_recall = recalls.iter().sum::<f64>() / recalls.len() as f64;

    println!(
        "{} ops in {:.2}s ({:.0} ops/s) | knn x{} | recall@{} {:.4} | p50 {:.3} ms | p99 {:.3} ms | ~{} engine bytes",
        wl.ops.len(),
        elapsed,
        wl.ops.len() as f64 / elapsed,
        knn_ms.len(),
        cfg.k,
        mean_recall,
        pct(0.50),
        pct(0.99),
        mt.approx_bytes()
    );

    // Emit one Pareto row so you can see the exact CSV schema in the wild.
    let row = ParetoRow {
        config_id: "memtable-bruteforce".into(),
        m: 0,
        ef_search: 0,
        rerank: false,
        codec: "f32".into(),
        recall_at_10: mean_recall,
        p50_ms: pct(0.50),
        p99_ms: pct(0.99),
        ops_per_sec: wl.ops.len() as f64 / elapsed,
        mem_bytes: mt.approx_bytes() as u64,
        commit: option_env!("GIT_COMMIT").unwrap_or("uncommitted").into(),
    };
    let mut w = csv::Writer::from_path("pareto.csv").expect("create pareto.csv");
    w.serialize(&row).expect("serialize row");
    w.flush().expect("flush csv");
    println!("wrote pareto.csv (schema demo — your sweep replaces this single row)");
}
