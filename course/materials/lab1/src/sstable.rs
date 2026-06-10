//! SSTable + vector segment: the on-disk format (normative spec) plus a
//! working writer. The reader is yours — Milestone 1.
//!
//! # File layout (NORMATIVE — graders parse your files against this)
//!
//! One `.sst` file holds six sections. All integers are **little-endian**.
//! Every section starts at a **64-byte-aligned** offset (pad the gap with
//! zero bytes), so a reader may hand out aligned zero-copy slices. The
//! footer is the last 64 bytes of the file: readers seek there first.
//!
//! ```text
//! offset 0                                                         EOF
//! ┌────────┬───────────┬─────────────┬───────────┬────────┬──────┬────────┐
//! │ header │ key block │ vector codes│ PQ        │ row-id │ HNSW │ footer │
//! │  32 B  │           │             │ codebook  │ map    │ adj. │  64 B  │
//! └────────┴───────────┴─────────────┴───────────┴────────┴──────┴────────┘
//! ```
//!
//! ## Section 0 — header, exactly 32 bytes at offset 0
//! | field        | type      | meaning                                      |
//! |--------------|-----------|----------------------------------------------|
//! | magic        | `[u8; 8]` | ASCII `VSST0001` — reject anything else      |
//! | version      | `u32`     | 1                                            |
//! | dim          | `u32`     | vector dimension D (768 for grading)         |
//! | entry_count  | `u32`     | rows in the key block                        |
//! | vector_count | `u32`     | N = vectors in the segment (may be 0)        |
//! | codec        | `u8`      | 0 = raw f32 · 1 = i8 scalar quant · 2 = PQ   |
//! | reserved     | `[u8; 7]` | zero; readers must ignore                    |
//!
//! ## Section 1 — key block: `entry_count` records, tightly packed
//! Record = 16-byte fixed prefix, then key bytes, then value bytes:
//! `key_len: u32 · val_len: u32 · flags: u32 (bit0 = tombstone) ·
//!  vec_ordinal: u32 (0xFFFF_FFFF = no vector)`.
//! Keys appear in strictly increasing lexicographic order. Tombstones have
//! `val_len = 0`. `vec_ordinal` indexes sections 2 and 4.
//!
//! ## Section 2 — vector codes, row-major, `N` rows of `D` codes
//! codec 0: `N × D × f32` (what this writer emits — your starting point).
//! codec 1: `N × D × i8`; code c decodes as `scale[j]·c + bias[j]` with
//!          per-dimension `scale`/`bias` stored in the codebook section.
//! codec 2: `N × M_pq × u8` PQ codes (one centroid id per subspace).
//!
//! ## Section 3 — codebook
//! Starts with `m_pq: u32 · k_pq: u32`.
//! codec 0: both zero, nothing follows.
//! codec 1: `m_pq = 0, k_pq = 0`, then `D × f32` scales, then `D × f32`
//!          biases (the affine decode params).
//! codec 2: then `m_pq × k_pq × (D / m_pq) × f32` centroids, subspace-major.
//!          `m_pq` must divide D; `k_pq ≤ 256`.
//!
//! ## Section 4 — row-id map: `N × u64`
//! `vec_ordinal → DocId`, sorted by ordinal (i.e. file order of section 2).
//!
//! ## Section 5 — HNSW adjacency (empty until Milestone 2)
//! `num_layers: u32 · entry_point: u32`, then per layer, top layer first:
//! `node_count: u32 · offsets: (node_count + 1) × u32 · neighbors: u32…`
//! — standard CSR: layer-l neighbors of node i are
//! `neighbors[offsets[i] .. offsets[i+1]]`, values are vec_ordinals.
//! `num_layers = 0` means "no graph"; this writer emits that.
//!
//! ## Footer — exactly 64 bytes at EOF − 64
//! `offsets: 6 × u64` (file offsets of sections 0–5) · `crc32c: u32` over
//! bytes `[0, footer_offset)` · `footer_magic: u32` = ASCII `VSFT` ·
//! `reserved: u64` zero.
//!
//! A conforming reader: seek to EOF−64 → check `footer_magic` → check CRC →
//! check header magic/version → validate every offset is 64-aligned,
//! monotonic, and in-bounds → only then trust any section. Return
//! [`Error::Corrupt`] on any violation. **Never panic on file bytes.**

use std::path::{Path, PathBuf};

use crate::{DocId, Error, Key, Result, Value, Vector};

pub const MAGIC: [u8; 8] = *b"VSST0001";
pub const VERSION: u32 = 1;
pub const FOOTER_LEN: usize = 64;
pub const FOOTER_MAGIC: u32 = u32::from_le_bytes(*b"VSFT");
pub const NO_VECTOR: u32 = u32::MAX;
pub const CODEC_RAW_F32: u8 = 0;
pub const CODEC_SQ_I8: u8 = 1;
pub const CODEC_PQ: u8 = 2;

/// CRC-32C (Castagnoli), bitwise reference implementation. Slow but
/// canonical; replace with the `crc32c` crate (allowed) if it shows up in
/// your profiles. Spec check: `crc32c(b"123456789") == 0xE306_9283`.
pub fn crc32c(data: &[u8]) -> u32 {
    let mut crc = !0u32;
    for &b in data {
        crc ^= b as u32;
        for _ in 0..8 {
            let mask = (crc & 1).wrapping_neg();
            crc = (crc >> 1) ^ (0x82F6_3B78 & mask);
        }
    }
    !crc
}

fn pad64(buf: &mut Vec<u8>) -> u64 {
    while !buf.len().is_multiple_of(64) {
        buf.push(0);
    }
    buf.len() as u64
}

#[derive(Clone, Debug)]
pub struct SstMeta {
    pub path: PathBuf,
    pub file_bytes: u64,
    pub entry_count: u32,
    pub vector_count: u32,
    pub smallest_key: Option<Key>,
    pub largest_key: Option<Key>,
}

struct PendingEntry {
    key: Key,
    value: Value,
    tombstone: bool,
    vec_ordinal: u32,
}

/// Buffers a whole table in memory, then writes it in one shot. COMPLETE
/// for codec 0 (raw f32). Milestone 1 extends `finish` to codec 1 — the
/// section skeleton below already leaves the right slots for it.
pub struct SstWriter {
    path: PathBuf,
    dim: u32,
    entries: Vec<PendingEntry>,
    codes: Vec<f32>,
    row_ids: Vec<DocId>,
}

impl SstWriter {
    pub fn new(path: impl Into<PathBuf>, dim: usize) -> SstWriter {
        SstWriter {
            path: path.into(),
            dim: dim as u32,
            entries: Vec::new(),
            codes: Vec::new(),
            row_ids: Vec::new(),
        }
    }

    fn check_order(&self, key: &[u8]) -> Result<()> {
        match self.entries.last() {
            Some(last) if key <= last.key.as_slice() => Err(Error::UnsortedKey),
            _ => Ok(()),
        }
    }

    /// Add a live row. Keys must arrive in strictly increasing order — feed
    /// this straight from `Memtable::iter_for_flush` (or a merge iterator).
    pub fn add(&mut self, key: &[u8], value: &[u8], vector: Option<(DocId, &Vector)>) -> Result<()> {
        self.check_order(key)?;
        let vec_ordinal = match vector {
            None => NO_VECTOR,
            Some((doc_id, v)) => {
                if v.dim() != self.dim as usize {
                    return Err(Error::DimMismatch { expected: self.dim as usize, got: v.dim() });
                }
                let ord = self.row_ids.len() as u32;
                self.row_ids.push(doc_id);
                self.codes.extend_from_slice(&v.0);
                ord
            }
        };
        self.entries.push(PendingEntry {
            key: key.to_vec(),
            value: value.to_vec(),
            tombstone: false,
            vec_ordinal,
        });
        Ok(())
    }

    pub fn add_tombstone(&mut self, key: &[u8]) -> Result<()> {
        self.check_order(key)?;
        self.entries.push(PendingEntry {
            key: key.to_vec(),
            value: Vec::new(),
            tombstone: true,
            vec_ordinal: NO_VECTOR,
        });
        Ok(())
    }

    pub fn finish(self) -> Result<SstMeta> {
        let mut buf: Vec<u8> = Vec::new();
        let mut offsets = [0u64; 6];

        // section 0: header
        buf.extend_from_slice(&MAGIC);
        buf.extend_from_slice(&VERSION.to_le_bytes());
        buf.extend_from_slice(&self.dim.to_le_bytes());
        buf.extend_from_slice(&(self.entries.len() as u32).to_le_bytes());
        buf.extend_from_slice(&(self.row_ids.len() as u32).to_le_bytes());
        buf.push(CODEC_RAW_F32);
        buf.extend_from_slice(&[0u8; 7]);
        debug_assert_eq!(buf.len(), 32);

        // section 1: key block
        offsets[1] = pad64(&mut buf);
        for e in &self.entries {
            buf.extend_from_slice(&(e.key.len() as u32).to_le_bytes());
            buf.extend_from_slice(&(e.value.len() as u32).to_le_bytes());
            buf.extend_from_slice(&(e.tombstone as u32).to_le_bytes());
            buf.extend_from_slice(&e.vec_ordinal.to_le_bytes());
            buf.extend_from_slice(&e.key);
            buf.extend_from_slice(&e.value);
        }

        // section 2: vector codes (codec 0 → raw f32)
        offsets[2] = pad64(&mut buf);
        for x in &self.codes {
            buf.extend_from_slice(&x.to_le_bytes());
        }

        // section 3: codebook (codec 0 → just the two zero counts)
        offsets[3] = pad64(&mut buf);
        buf.extend_from_slice(&0u32.to_le_bytes()); // m_pq
        buf.extend_from_slice(&0u32.to_le_bytes()); // k_pq

        // section 4: row-id map
        offsets[4] = pad64(&mut buf);
        for id in &self.row_ids {
            buf.extend_from_slice(&id.to_le_bytes());
        }

        // section 5: HNSW adjacency (empty until Milestone 2)
        offsets[5] = pad64(&mut buf);
        buf.extend_from_slice(&0u32.to_le_bytes()); // num_layers
        buf.extend_from_slice(&0u32.to_le_bytes()); // entry_point

        // footer
        let footer_off = pad64(&mut buf);
        let crc = crc32c(&buf);
        for off in offsets {
            buf.extend_from_slice(&off.to_le_bytes());
        }
        buf.extend_from_slice(&crc.to_le_bytes());
        buf.extend_from_slice(&FOOTER_MAGIC.to_le_bytes());
        buf.extend_from_slice(&0u64.to_le_bytes());
        debug_assert_eq!(buf.len() as u64 - footer_off, FOOTER_LEN as u64);

        let meta = SstMeta {
            file_bytes: buf.len() as u64,
            entry_count: self.entries.len() as u32,
            vector_count: self.row_ids.len() as u32,
            smallest_key: self.entries.first().map(|e| e.key.clone()),
            largest_key: self.entries.last().map(|e| e.key.clone()),
            path: self.path,
        };
        std::fs::write(&meta.path, &buf)?;
        Ok(meta)
    }
}

/// YOU WRITE (Milestone 1). Choose your own representation: the reference
/// solution holds the whole file as `Vec<u8>` (switch to `memmap2` later)
/// plus the six validated section offsets.
pub struct SstReader {
    _todo: (),
}

#[allow(unused_variables)]
impl SstReader {
    /// Open and *fully validate* the file.
    ///
    /// HINTS — the validation order that avoids UB and panics:
    ///   1. file len ≥ 32 + 64, else Corrupt.
    ///   2. read last 64 B; check `FOOTER_MAGIC`; check `crc32c` over
    ///      `[0, len − 64)` against the stored CRC.
    ///   3. check header `MAGIC`/`VERSION`; check each of the 6 offsets is
    ///      64-aligned, monotonically increasing, and < len − 64.
    ///   4. walk the key block once, bounds-checking every record against
    ///      `entry_count`, the section end, and `vector_count`
    ///      (`vec_ordinal < vector_count || vec_ordinal == NO_VECTOR`).
    ///
    /// The mutated-file corpus in the grading suite exercises every one of
    /// these checks individually.
    pub fn open(path: &Path) -> Result<SstReader> {
        todo!("Milestone 1: parse + validate footer, header, sections")
    }

    /// Point lookup. HINT: keys are sorted, but records are variable-length
    /// — either scan linearly (fine to start) or build an in-memory offset
    /// index in `open` (do this before Milestone 4, your p99 will thank you).
    pub fn get(&self, key: &[u8]) -> Result<Option<Value>> {
        todo!("Milestone 1: binary/linear search of the key block")
    }

    /// Range scan over `[start, end)`, ascending, tombstones included —
    /// the caller (your `Db`) merges levels and drops shadowed rows.
    pub fn scan(&self, start: &[u8], end: &[u8]) -> Result<Vec<(Key, Option<Value>)>> {
        todo!("Milestone 1: iterate key block within bounds")
    }

    /// Decode the vector for one ordinal (codec-aware). Used by the recall
    /// oracle and by f32 re-ranking.
    pub fn vector(&self, ordinal: u32) -> Result<Vector> {
        todo!("Milestone 1: slice section 2, apply codec decode")
    }

    /// Beam search over this segment's HNSW graph; nearest first, distances
    /// computed on the *quantized* codes. HINT: `ef ≥ k`; filter nothing
    /// here — shadow/tombstone filtering happens above, in `Db::knn`.
    pub fn knn(&self, query: &Vector, k: usize, ef: usize) -> Result<Vec<(DocId, f32)>> {
        todo!("Milestone 2: HNSW beam search over the CSR adjacency")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Vector;

    #[test]
    fn crc32c_matches_spec_vector() {
        assert_eq!(crc32c(b"123456789"), 0xE306_9283);
    }

    #[test]
    fn writer_emits_aligned_checksummed_layout() {
        let path = std::env::temp_dir().join("vlsm_writer_layout_test.sst");
        let mut w = SstWriter::new(&path, 4);
        let v = Vector(vec![0.1, 0.2, 0.3, 0.4]);
        w.add(b"alpha", b"v1", Some((7, &v))).unwrap();
        w.add(b"beta", b"v2", None).unwrap();
        w.add_tombstone(b"gamma").unwrap();
        assert!(matches!(w.add(b"beta", b"", None), Err(Error::UnsortedKey)));
        let meta = w.finish().unwrap();

        let bytes = std::fs::read(&meta.path).unwrap();
        assert_eq!(&bytes[..8], MAGIC.as_slice());
        assert_eq!(bytes.len() % 64, 0);
        assert_eq!((meta.entry_count, meta.vector_count), (3, 1));

        let foot = &bytes[bytes.len() - FOOTER_LEN..];
        let stored_crc = u32::from_le_bytes(foot[48..52].try_into().unwrap());
        assert_eq!(stored_crc, crc32c(&bytes[..bytes.len() - FOOTER_LEN]));
        assert_eq!(u32::from_le_bytes(foot[52..56].try_into().unwrap()), FOOTER_MAGIC);
        // every section offset 64-aligned
        for i in 0..6 {
            let off = u64::from_le_bytes(foot[i * 8..i * 8 + 8].try_into().unwrap());
            assert_eq!(off % 64, 0, "section {i} misaligned");
        }
        std::fs::remove_file(&meta.path).ok();
    }
}
