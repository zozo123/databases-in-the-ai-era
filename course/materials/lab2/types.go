// Package lab2 is the starter skeleton for DATA 2027 Lab 2 ("Mini-Neon").
//
// This file is COMPLETE and normative: the conformance vectors and the
// grader assume the wire format and object-key grammar below. Do not change.
package lab2

import (
	"encoding/binary"
	"fmt"
	"hash/crc32"
	"strings"
	"time"
)

// LSN is a log sequence number: a byte offset into a timeline's WAL stream.
// LSNs are strictly increasing within a timeline. We print them in hex
// (0x10, 0x5a, ...) to match the lab handout.
type LSN uint64

// PageID is a page number in the flat page space 0..N-1.
type PageID uint32

// TimelineID names a timeline (a branch's private WAL history). main's
// timeline is "main"; child timelines are the branch UUID.
type TimelineID string

func (l LSN) String() string { return fmt.Sprintf("0x%x", uint64(l)) }

// PageSize is fixed for the whole lab. A FULL_PAGE payload is exactly this long.
const PageSize = 8192

// WAL record kinds (the `kind` byte on the wire).
const (
	KindFullPage uint8 = 1 // payload is an 8192-byte page image
	KindDelta    uint8 = 2 // payload is k x { uint16 off; uint16 len; len bytes }
)

// walHeaderSize is the fixed prefix before the payload:
// 8 (lsn) + 4 (page_no) + 1 (kind) + 2 (plen).
const walHeaderSize = 15

// crc32c (Castagnoli), as stated in the handout. Plain crc32 (IEEE) will pass
// your own tests and fail every conformance vector — use this table.
var castagnoli = crc32.MakeTable(crc32.Castagnoli)

// WalRecord is one record of the WAL stream, exactly as emitted by walgen.
//
// Wire format (little-endian):
//
//	off       size  field
//	0         8     lsn       uint64
//	8         4     page_no   uint32
//	12        1     kind      uint8 (1=FULL_PAGE, 2=DELTA)
//	13        2     plen      uint16
//	15        plen  payload
//	15+plen   4     crc32c    over bytes [0, 15+plen)
type WalRecord struct {
	LSN      LSN
	PageID   PageID
	Kind     uint8
	Payload  []byte
	Checksum uint32 // crc32c of header+payload; filled in by Encode/Decode
}

// EncodedSize returns the on-wire length of the record.
func (r *WalRecord) EncodedSize() int { return walHeaderSize + len(r.Payload) + 4 }

// Encode serializes the record, computing and storing the checksum.
func (r *WalRecord) Encode() []byte {
	if len(r.Payload) > 0xffff {
		panic(fmt.Sprintf("wal record payload too large: %d", len(r.Payload)))
	}
	buf := make([]byte, r.EncodedSize())
	binary.LittleEndian.PutUint64(buf[0:8], uint64(r.LSN))
	binary.LittleEndian.PutUint32(buf[8:12], uint32(r.PageID))
	buf[12] = r.Kind
	binary.LittleEndian.PutUint16(buf[13:15], uint16(len(r.Payload)))
	copy(buf[walHeaderSize:], r.Payload)
	r.Checksum = crc32.Checksum(buf[:walHeaderSize+len(r.Payload)], castagnoli)
	binary.LittleEndian.PutUint32(buf[walHeaderSize+len(r.Payload):], r.Checksum)
	return buf
}

// DecodeWalRecord parses one record from the front of b. It returns the
// record, the number of bytes consumed, and an error on truncation, an
// unknown kind, or a checksum mismatch (which you must reject, per M1).
func DecodeWalRecord(b []byte) (WalRecord, int, error) {
	if len(b) < walHeaderSize {
		return WalRecord{}, 0, fmt.Errorf("wal: truncated header (%d bytes)", len(b))
	}
	plen := int(binary.LittleEndian.Uint16(b[13:15]))
	total := walHeaderSize + plen + 4
	if len(b) < total {
		return WalRecord{}, 0, fmt.Errorf("wal: truncated record (want %d, have %d)", total, len(b))
	}
	rec := WalRecord{
		LSN:     LSN(binary.LittleEndian.Uint64(b[0:8])),
		PageID:  PageID(binary.LittleEndian.Uint32(b[8:12])),
		Kind:    b[12],
		Payload: append([]byte(nil), b[walHeaderSize:walHeaderSize+plen]...),
	}
	if rec.Kind != KindFullPage && rec.Kind != KindDelta {
		return WalRecord{}, 0, fmt.Errorf("wal: unknown kind %d at lsn %s", rec.Kind, rec.LSN)
	}
	want := binary.LittleEndian.Uint32(b[walHeaderSize+plen : total])
	got := crc32.Checksum(b[:walHeaderSize+plen], castagnoli)
	if want != got {
		return WalRecord{}, 0, fmt.Errorf("wal: crc mismatch at lsn %s: want %08x got %08x", rec.LSN, want, got)
	}
	rec.Checksum = want
	return rec, total, nil
}

// DeltaSpan is one edit inside a DELTA payload: overwrite page[Off:Off+len(Data)].
type DeltaSpan struct {
	Off  uint16
	Data []byte
}

// EncodeDeltaPayload packs spans into the DELTA payload wire form.
// Used by walgen; you may also find it handy in tests.
func EncodeDeltaPayload(spans []DeltaSpan) []byte {
	var out []byte
	for _, s := range spans {
		var hdr [4]byte
		binary.LittleEndian.PutUint16(hdr[0:2], s.Off)
		binary.LittleEndian.PutUint16(hdr[2:4], uint16(len(s.Data)))
		out = append(out, hdr[:]...)
		out = append(out, s.Data...)
	}
	return out
}

// DecodeDeltaPayload unpacks a DELTA payload. Spans are applied in order.
func DecodeDeltaPayload(payload []byte) ([]DeltaSpan, error) {
	var spans []DeltaSpan
	for i := 0; i < len(payload); {
		if len(payload)-i < 4 {
			return nil, fmt.Errorf("delta: truncated span header at %d", i)
		}
		off := binary.LittleEndian.Uint16(payload[i : i+2])
		n := int(binary.LittleEndian.Uint16(payload[i+2 : i+4]))
		i += 4
		if len(payload)-i < n || int(off)+n > PageSize {
			return nil, fmt.Errorf("delta: span out of bounds (off=%d len=%d)", off, n)
		}
		spans = append(spans, DeltaSpan{Off: off, Data: append([]byte(nil), payload[i:i+n]...)})
		i += n
	}
	return spans, nil
}

// ---------------------------------------------------------------------------
// Layer files
// ---------------------------------------------------------------------------

// LayerKind distinguishes the two layer-file shapes in the key x LSN plane.
type LayerKind uint8

const (
	// LayerImage: full 8 KiB images of every page in [KeyLo, KeyHi] as of a
	// single LSN (LsnLo == LsnHi == the snapshot LSN). A vertical slice.
	LayerImage LayerKind = 1
	// LayerDelta: every WAL record with page in [KeyLo, KeyHi] and
	// lsn in (LsnLo, LsnHi], sorted by (page_no, lsn). A rectangle.
	LayerDelta LayerKind = 2
)

// LayerFileMeta identifies one immutable layer object in the bucket.
// The interior encoding of the object is YOUR design; the object key is not.
//
// Normative key grammar (all numbers lower-case hex, no 0x prefix):
//
//	tl/<timeline_id>/img__<keylo>-<keyhi>__<lsn>
//	tl/<timeline_id>/del__<keylo>-<keyhi>__<lsnlo>-<lsnhi>
//
// Key ranges are inclusive on both ends; delta LSN ranges are (lo, hi].
type LayerFileMeta struct {
	Timeline TimelineID
	Kind     LayerKind
	KeyLo    PageID
	KeyHi    PageID
	LsnLo    LSN // images: equal to LsnHi
	LsnHi    LSN
}

// ObjectKey renders the normative bucket key for this layer.
func (m LayerFileMeta) ObjectKey() string {
	switch m.Kind {
	case LayerImage:
		return fmt.Sprintf("tl/%s/img__%x-%x__%x", m.Timeline, m.KeyLo, m.KeyHi, uint64(m.LsnHi))
	case LayerDelta:
		return fmt.Sprintf("tl/%s/del__%x-%x__%x-%x", m.Timeline, m.KeyLo, m.KeyHi, uint64(m.LsnLo), uint64(m.LsnHi))
	default:
		panic("unknown layer kind")
	}
}

// ParseLayerKey is the inverse of ObjectKey. Useful for debugging and for
// rebuilding a layer map from a manifest (NOT from LIST — see the handout).
func ParseLayerKey(key string) (LayerFileMeta, error) {
	var m LayerFileMeta
	parts := strings.Split(key, "/")
	if len(parts) != 3 || parts[0] != "tl" {
		return m, fmt.Errorf("layer key %q: want tl/<timeline>/<name>", key)
	}
	m.Timeline = TimelineID(parts[1])
	name := parts[2]
	switch {
	case strings.HasPrefix(name, "img__"):
		m.Kind = LayerImage
		var lsn uint64
		if _, err := fmt.Sscanf(name, "img__%x-%x__%x", &m.KeyLo, &m.KeyHi, &lsn); err != nil {
			return m, fmt.Errorf("layer key %q: %w", key, err)
		}
		m.LsnLo, m.LsnHi = LSN(lsn), LSN(lsn)
	case strings.HasPrefix(name, "del__"):
		m.Kind = LayerDelta
		var lo, hi uint64
		if _, err := fmt.Sscanf(name, "del__%x-%x__%x-%x", &m.KeyLo, &m.KeyHi, &lo, &hi); err != nil {
			return m, fmt.Errorf("layer key %q: %w", key, err)
		}
		m.LsnLo, m.LsnHi = LSN(lo), LSN(hi)
	default:
		return m, fmt.Errorf("layer key %q: unknown layer name", key)
	}
	return m, nil
}

// ContainsKey reports whether page p falls inside this layer's key range.
// (Deliberately no CoversLSN helper: the <= vs < decisions at LSN boundaries
// are yours to make, write down, and use consistently — see Pitfalls.)
func (m LayerFileMeta) ContainsKey(p PageID) bool { return m.KeyLo <= p && p <= m.KeyHi }

// ---------------------------------------------------------------------------
// Branches
// ---------------------------------------------------------------------------

// BranchState is the lifecycle of a branch as GC sees it.
type BranchState string

const (
	BranchLive BranchState = "live"
	BranchDead BranchState = "dead" // set by DeleteBranch; GC reclaims later
)

// BranchMeta is the authoritative branch record, stored as JSON at
// branches/<name>.json. It is the ONLY source of truth for which branches
// exist — never S3 LIST. ID doubles as the branch's TimelineID.
type BranchMeta struct {
	ID         TimelineID  `json:"branch_id"`
	Name       string      `json:"name"`
	ParentID   TimelineID  `json:"parent_id,omitempty"` // empty only for main
	ForkLSN    LSN         `json:"fork_lsn"`            // reads at lsn <= ForkLSN resolve via ancestors
	HeadLSN    LSN         `json:"head_lsn"`            // highest durable LSN on this timeline
	State      BranchState `json:"state"`
	CreatedAt  time.Time   `json:"created_at"`
	LastReadAt time.Time   `json:"last_read_at"` // input to your abandonment policy (M4)
}

// BranchKey returns the bucket key for a branch's metadata object.
func BranchKey(name string) string { return "branches/" + name + ".json" }
