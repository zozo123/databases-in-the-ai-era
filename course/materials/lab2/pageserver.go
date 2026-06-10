package lab2

// This file is YOURS to implement (Milestones 1 and 2). The signatures and
// the resolution algorithm in the comments are normative; the data structures
// behind them are suggestions — replace them if you have a better design,
// but keep the public method signatures so the harness can drive you.

import (
	"context"
	"fmt"
	"sync"
)

// Pageserver ingests WAL, seals it into immutable layer objects, and answers
// GetPage(branch, page, lsn). All durable state lives in the ObjectStore;
// everything in this struct must be reconstructible from the bucket after a
// crash (the grader deletes your local state mid-run and re-reads).
type Pageserver struct {
	store ObjectStore

	mu sync.RWMutex
	// branches caches branch metadata by name. The bucket objects under
	// branches/ are the authority; this map is a cache, refreshed on miss.
	branches map[string]*BranchMeta
	// layers is the layer map: for each timeline, the metadata of every
	// sealed layer object, kept sorted however your resolution likes it.
	// Rebuild it from your durable manifest on startup — not from LIST.
	layers map[TimelineID][]LayerFileMeta
	// open holds the in-memory open (unsealed) layer per timeline: records
	// ingested but not yet flushed. Lost on crash by design; durability of
	// unflushed WAL may rely on a local append-only log (see handout).
	open map[TimelineID][]WalRecord
}

// NewPageserver attaches to a bucket and rebuilds in-memory state from
// durable metadata. After this returns, GetPage must answer correctly for
// everything that was flushed before a crash.
func NewPageserver(ctx context.Context, store ObjectStore) (*Pageserver, error) {
	ps := &Pageserver{
		store:    store,
		branches: make(map[string]*BranchMeta),
		layers:   make(map[TimelineID][]LayerFileMeta),
		open:     make(map[TimelineID][]WalRecord),
	}
	// TODO(M1): load branch metadata and your layer manifest(s) from the
	// bucket and populate ps.branches / ps.layers. Design question you must
	// answer here: where is the manifest, and how is it committed atomically
	// relative to layer PUTs? (Pitfall: "LIST is not a catalog".)
	_ = ctx
	return ps, nil
}

// IngestWAL validates one encoded record (CRC!), appends it to the open
// layer of the given timeline, and returns the LSN of the last record that
// is DURABLE (not merely buffered). Records arrive in strictly increasing
// LSN order per timeline; reject out-of-order or corrupt records.
func (ps *Pageserver) IngestWAL(ctx context.Context, tl TimelineID, rec []byte) (LSN, error) {
	// TODO(M1): DecodeWalRecord (it already verifies crc32c), check LSN
	// monotonicity against the open layer / head, append, maybe auto-flush
	// when the open layer crosses your size threshold.
	return 0, fmt.Errorf("IngestWAL: not implemented")
}

// Flush seals every open layer into a delta-layer object, PUTs it (key from
// LayerFileMeta.ObjectKey), commits the manifest, and advances head LSNs.
// After Flush returns, a brand-new Pageserver on the same bucket must serve
// every flushed record. Order matters: layer PUT, then manifest, then head —
// think through what each crash window leaves behind.
func (ps *Pageserver) Flush(ctx context.Context) error {
	// TODO(M1)
	return fmt.Errorf("Flush: not implemented")
}

// GetPage returns the exact 8192-byte image of page p on `branch` as of LSN
// `at`. This is THE primitive of the lab; everything else exists so this can
// be fast and correct.
//
// Normative resolution algorithm (handout, Milestone 2 — M3 adds step 3):
//
//  1. T <- the branch's timeline; L <- min(at, head_lsn(T)).
//
//  2. In T's layer map, find the newest image layer containing p with
//     lsn <= L (call its LSN L_img; it may not exist). Collect every delta
//     layer whose key range contains p and whose LSN range intersects
//     (L_img, L]. You will replay only records for p with lsn in (L_img, L].
//
//  3. If T has no covering image layer and T has a parent:
//     L <- min(L, fork_lsn(T)); T <- parent(T); go to step 2, accumulating
//     deltas as you go. (Reads at lsn <= fork_lsn are ancestor reads — the
//     child's own layers must NOT be consulted below its fork point.)
//
//  4. Start from the base image (the image layer's page, or a FULL_PAGE
//     record if one appears in the collected range) and call
//     materializePage to replay the deltas in ascending LSN order.
//
// Cost contract: the number of store GETs is bounded by the number of layers
// intersected in step 2/3 — never by total WAL length. The grader counts.
//
// Boundary semantics (most wrong-read bugs live here): a record at LSN n is
// visible AT n; delta ranges are (lo, hi]; at a fork, lsn == fork_lsn
// resolves through the PARENT-inclusive side. Decide <= vs < once, write it
// down, use it everywhere.
func (ps *Pageserver) GetPage(ctx context.Context, branch string, p PageID, at LSN) ([]byte, error) {
	// TODO(M2): implement the walk above. Suggested shape:
	//
	//   meta, err := ps.lookupBranch(ctx, branch)
	//   tl, limit := meta.ID, min(at, meta.HeadLSN)
	//   var pending []WalRecord            // newest timeline first
	//   var base []byte                    // image-layer page, once found
	//   for {
	//       img, deltas := ps.coveringLayers(tl, p, limit)  // layer-map query
	//       ... fetch the page's records from each delta object ...
	//       if img found { base = fetch page from image object; break }
	//       parent, forkLSN, ok := ps.parentOf(tl)
	//       if !ok { break } // main with no image: base is a FULL_PAGE record
	//       limit = min(limit, forkLSN); tl = parent
	//   }
	//   return ps.materializePage(base, pendingInAscendingLSN)
	//
	// Remember the crash test: nothing here may depend on warm process state
	// that NewPageserver cannot rebuild.
	return nil, fmt.Errorf("GetPage: not implemented")
}

// materializePage replays WAL records onto a base image and returns the
// final 8192-byte page.
//
//   - base is the starting image (from an image layer), or nil if the oldest
//     collected record is a FULL_PAGE record (which then becomes the base).
//   - records must be for a single page, in ascending LSN order; a FULL_PAGE
//     record replaces the whole image, a DELTA record applies its spans
//     (DecodeDeltaPayload) in order.
//
// Errors: nil base with no leading FULL_PAGE record means the caller's layer
// walk was wrong — fail loudly, do not return zeroes.
func (ps *Pageserver) materializePage(base []byte, records []WalRecord) ([]byte, error) {
	// TODO(M1): straightforward replay. ~20 lines.
	return nil, fmt.Errorf("materializePage: not implemented")
}

// lookupBranch returns branch metadata by name, consulting the cache first
// and falling back to the authoritative branches/<name>.json object.
func (ps *Pageserver) lookupBranch(ctx context.Context, name string) (*BranchMeta, error) {
	// TODO(M3): cache + store.Get(BranchKey(name)) + json.Unmarshal.
	return nil, fmt.Errorf("lookupBranch: not implemented")
}
