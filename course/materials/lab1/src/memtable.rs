//! In-memory write buffer. COMPLETE — you should not need to modify it,
//! only call it (and flush it through your `SstWriter`).
//!
//! Keys live in a `BTreeMap` so `scan` is a range query; vectors live in a
//! side-table keyed by `DocId` so `knn` never walks the key map. An upsert
//! retires the old version's doc id (and its vector) immediately — the
//! memtable holds only the latest version of each key.

use std::collections::BTreeMap;
use std::ops::Bound;

use crate::{DocId, Engine, Error, Key, Result, Value, Vector};

/// Bookkeeping bytes charged per entry on top of key/value/vector payloads.
/// Crude, but consistent — flush sizing only needs to be roughly right.
const ENTRY_OVERHEAD: usize = 48;

#[derive(Clone, Debug)]
enum Slot {
    Put { value: Value, doc_id: Option<DocId> },
    Tombstone,
}

/// One entry of [`Memtable::iter_for_flush`], in ascending key order.
/// `value == None` means tombstone (your SSTable must persist these!).
pub struct FlushEntry<'a> {
    pub key: &'a [u8],
    pub value: Option<&'a [u8]>,
    pub vector: Option<(DocId, &'a Vector)>,
}

pub struct Memtable {
    map: BTreeMap<Key, Slot>,
    vectors: BTreeMap<DocId, Vector>,
    next_doc_id: DocId,
    approx_bytes: usize,
    dim: usize,
}

impl Memtable {
    pub fn new(dim: usize) -> Memtable {
        Memtable::with_first_doc_id(dim, 0)
    }

    /// A fresh memtable that continues the doc-id sequence — use this after
    /// a flush so ids stay globally unique across the engine's lifetime.
    pub fn with_first_doc_id(dim: usize, first_doc_id: DocId) -> Memtable {
        Memtable {
            map: BTreeMap::new(),
            vectors: BTreeMap::new(),
            next_doc_id: first_doc_id,
            approx_bytes: 0,
            dim,
        }
    }

    pub fn len(&self) -> usize {
        self.map.len()
    }

    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    pub fn approx_bytes(&self) -> usize {
        self.approx_bytes
    }

    pub fn is_full(&self, max_bytes: usize) -> bool {
        self.approx_bytes >= max_bytes
    }

    /// The next doc id this memtable would assign (pass it to the successor
    /// memtable at flush time).
    pub fn next_doc_id(&self) -> DocId {
        self.next_doc_id
    }

    fn check_dim(&self, v: &Vector) -> Result<()> {
        if v.dim() == self.dim {
            Ok(())
        } else {
            Err(Error::DimMismatch { expected: self.dim, got: v.dim() })
        }
    }

    /// Record a tombstone for `key`. (Kept off the `Engine` trait: the Lab 1
    /// workload has no deletes, but flush/compaction tests use this.)
    pub fn delete(&mut self, key: &[u8]) -> Result<()> {
        self.approx_bytes += key.len() + ENTRY_OVERHEAD;
        if let Some(Slot::Put { doc_id: Some(old), .. }) =
            self.map.insert(key.to_vec(), Slot::Tombstone)
        {
            self.vectors.remove(&old);
        }
        Ok(())
    }

    /// Everything a flush needs, in key order. Feed each entry to your
    /// `SstWriter`: `add(key, value, vector)` or `add_tombstone(key)`.
    pub fn iter_for_flush(&self) -> impl Iterator<Item = FlushEntry<'_>> {
        self.map.iter().map(move |(key, slot)| match slot {
            Slot::Tombstone => FlushEntry { key, value: None, vector: None },
            Slot::Put { value, doc_id } => FlushEntry {
                key,
                value: Some(value.as_slice()),
                vector: doc_id.map(|id| {
                    (id, self.vectors.get(&id).expect("side-table invariant broken"))
                }),
            },
        })
    }
}

impl Engine for Memtable {
    fn put(&mut self, key: Key, value: Value, vector: Option<Vector>) -> Result<Option<DocId>> {
        if let Some(v) = vector.as_ref() {
            self.check_dim(v)?;
        }
        self.approx_bytes += key.len()
            + value.len()
            + ENTRY_OVERHEAD
            + vector.as_ref().map_or(0, |v| v.dim() * 4);
        let doc_id = vector.as_ref().map(|_| {
            let id = self.next_doc_id;
            self.next_doc_id += 1;
            id
        });
        if let (Some(id), Some(v)) = (doc_id, vector) {
            self.vectors.insert(id, v);
        }
        // The old version (if any) dies here: drop its vector so knn only
        // ever sees live rows.
        if let Some(Slot::Put { doc_id: Some(old), .. }) =
            self.map.insert(key, Slot::Put { value, doc_id })
        {
            self.vectors.remove(&old);
        }
        Ok(doc_id)
    }

    fn get(&self, key: &[u8]) -> Result<Option<Value>> {
        Ok(match self.map.get(key) {
            Some(Slot::Put { value, .. }) => Some(value.clone()),
            Some(Slot::Tombstone) | None => None,
        })
    }

    fn scan(&self, start: &[u8], end: &[u8]) -> Result<Vec<(Key, Value)>> {
        let range = (Bound::Included(start), Bound::Excluded(end));
        Ok(self
            .map
            .range::<[u8], _>(range)
            .filter_map(|(k, slot)| match slot {
                Slot::Put { value, .. } => Some((k.clone(), value.clone())),
                Slot::Tombstone => None,
            })
            .collect())
    }

    /// Exact brute-force search — the memtable is small, so this is both the
    /// correct answer and fast enough. `ef` is ignored.
    fn knn(&self, query: &Vector, k: usize, _ef: usize) -> Result<Vec<(DocId, f32)>> {
        self.check_dim(query)?;
        let mut cands: Vec<(DocId, f32)> = self
            .vectors
            .iter()
            .map(|(&id, v)| (id, query.cosine_distance(v)))
            .collect();
        // Ties broken by doc id so results are fully deterministic.
        cands.sort_by(|a, b| {
            a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal).then(a.0.cmp(&b.0))
        });
        cands.truncate(k);
        Ok(cands)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(xs: &[f32]) -> Vector {
        let mut v = Vector(xs.to_vec());
        v.normalize();
        v
    }

    #[test]
    fn upsert_delete_scan_semantics() {
        let mut mt = Memtable::new(2);
        let id0 = mt.put(b"k".to_vec(), b"v0".to_vec(), Some(v(&[1.0, 0.0]))).unwrap();
        let id1 = mt.put(b"k".to_vec(), b"v1".to_vec(), Some(v(&[0.0, 1.0]))).unwrap();
        assert_eq!((id0, id1), (Some(0), Some(1)));
        assert_eq!(mt.get(b"k").unwrap(), Some(b"v1".to_vec()));
        // Only the live version is searchable.
        let hits = mt.knn(&v(&[1.0, 0.0]), 10, 0).unwrap();
        assert_eq!(hits.iter().map(|&(id, _)| id).collect::<Vec<_>>(), vec![1]);
        // Tombstones hide rows from scan but still reach the flush iterator.
        mt.put(b"m".to_vec(), b"vm".to_vec(), None).unwrap();
        mt.delete(b"k").unwrap();
        assert_eq!(mt.scan(b"a", b"z").unwrap(), vec![(b"m".to_vec(), b"vm".to_vec())]);
        assert_eq!(mt.iter_for_flush().count(), 2);
        assert!(mt.knn(&v(&[1.0, 0.0]), 10, 0).unwrap().is_empty());
    }
}
