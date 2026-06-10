// walgen is the deterministic WAL generator for Lab 2. It is COMPLETE — do
// not modify it; the grading seeds (1-9) must produce byte-identical output
// on every machine.
//
// It writes a workload directory:
//
//	<out>/main.wal          WAL stream for timeline "main" (wire format, types.go)
//	<out>/agent-XXX.wal     one stream per child branch, records above its fork
//	<out>/plan.json         the branch topology: name, parent, fork_lsn, files
//
// Usage (the 50-branch swarm workload on grading seed 1):
//
//	go run ./cmd/walgen -seed 1 -pages 1024 -records 20000 -branches 50 -out ./workload
//
// Your driver feeds main.wal into IngestWAL("main", ...), flushes, then for
// each plan entry calls CreateBranch(name, "main", fork_lsn) and ingests the
// branch's .wal into its own timeline. One private SplitMix64 stream drives
// everything in fixed order, so identical flags give identical bytes on any
// machine and Go version.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"

	lab2 "data2027/lab2"
)

type planBranch struct {
	Name    string   `json:"name"`
	Parent  string   `json:"parent"`
	ForkLSN lab2.LSN `json:"fork_lsn"`
	WalFile string   `json:"wal_file"`
	Records int      `json:"records"`
	HeadLSN lab2.LSN `json:"head_lsn"`
}

type plan struct {
	Seed     int64        `json:"seed"`
	Pages    int          `json:"pages"`
	Main     planBranch   `json:"main"`
	Branches []planBranch `json:"branches"`
}

func main() {
	log.SetFlags(0)
	seed := flag.Int64("seed", 1, "rng seed; grading uses seeds 1-9")
	pages := flag.Int("pages", 1024, "size of the flat page space (pages 0..N-1)")
	records := flag.Int("records", 20000, "WAL records on timeline main")
	branches := flag.Int("branches", 50, "child branches forking from main at random LSNs")
	branchRecords := flag.Int("branch-records", 200, "WAL records per child branch")
	out := flag.String("out", "./workload", "output directory")
	flag.Parse()

	if err := run(*seed, *pages, *records, *branches, *branchRecords, *out); err != nil {
		log.Fatalf("walgen: %v", err)
	}
}

func run(seed int64, pages, records, branches, branchRecords int, out string) error {
	if pages <= 0 || records <= 0 || branches < 0 || branchRecords < 0 {
		return fmt.Errorf("all sizes must be positive")
	}
	if err := os.MkdirAll(out, 0o755); err != nil {
		return err
	}
	rng := newRng(seed)

	// --- timeline main -----------------------------------------------------
	// firstTouch[p] = LSN of p's first (FULL_PAGE) record on main. A child
	// forking at f has a materializable base for p iff firstTouch[p] <= f.
	firstTouch := make(map[lab2.PageID]lab2.LSN)
	mainLSNs := make([]lab2.LSN, 0, records) // every record LSN, for fork sampling
	var lsn lab2.LSN

	mainRecs := make([]byte, 0, records*64)
	for i := 0; i < records; i++ {
		p := lab2.PageID(rng.intn(pages))
		var rec lab2.WalRecord
		if _, seen := firstTouch[p]; !seen {
			rec = fullPageRecord(rng, p)
		} else {
			rec = deltaRecord(rng, p)
		}
		lsn += lab2.LSN(rec.EncodedSize()) // LSN = end offset of the record, Postgres-style
		rec.LSN = lsn
		if _, seen := firstTouch[p]; !seen {
			firstTouch[p] = lsn
		}
		mainRecs = append(mainRecs, rec.Encode()...)
		mainLSNs = append(mainLSNs, lsn)
	}
	if err := os.WriteFile(filepath.Join(out, "main.wal"), mainRecs, 0o644); err != nil {
		return err
	}
	pl := plan{
		Seed:  seed,
		Pages: pages,
		Main:  planBranch{Name: "main", Parent: "", ForkLSN: 0, WalFile: "main.wal", Records: records, HeadLSN: lsn},
	}

	// --- child branches ----------------------------------------------------
	// Fork points are sampled from the middle 80% of main's history so every
	// branch has both ancestry below it and parent WAL above it (the reads
	// that catch <= vs < bugs at fork boundaries).
	for b := 0; b < branches; b++ {
		name := fmt.Sprintf("agent-%03d", b+1)
		lo, hi := records/10, records-records/10
		fork := mainLSNs[lo+rng.intn(hi-lo)]

		// Pages with a base visible at the fork point.
		based := make(map[lab2.PageID]bool)
		for p, fl := range firstTouch {
			if fl <= fork {
				based[p] = true
			}
		}
		cur := fork
		recs := make([]byte, 0, branchRecords*64)
		for i := 0; i < branchRecords; i++ {
			p := lab2.PageID(rng.intn(pages))
			var rec lab2.WalRecord
			if based[p] {
				rec = deltaRecord(rng, p)
			} else {
				rec = fullPageRecord(rng, p)
				based[p] = true
			}
			cur += lab2.LSN(rec.EncodedSize())
			rec.LSN = cur
			recs = append(recs, rec.Encode()...)
		}
		file := name + ".wal"
		if err := os.WriteFile(filepath.Join(out, file), recs, 0o644); err != nil {
			return err
		}
		pl.Branches = append(pl.Branches, planBranch{
			Name: name, Parent: "main", ForkLSN: fork,
			WalFile: file, Records: branchRecords, HeadLSN: cur,
		})
	}
	planBytes, err := json.MarshalIndent(pl, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(out, "plan.json"), append(planBytes, '\n'), 0o644); err != nil {
		return err
	}
	log.Printf("walgen: seed=%d pages=%d -> %s", seed, pages, out)
	log.Printf("  main: %d records, head %s (%d bytes)", records, lsn, len(mainRecs))
	log.Printf("  %d branches x %d records each; topology in plan.json", branches, branchRecords)
	return nil
}

// fullPageRecord emits a FULL_PAGE record with a deterministic 8 KiB image.
func fullPageRecord(r *rng, p lab2.PageID) lab2.WalRecord {
	img := make([]byte, lab2.PageSize)
	r.fill(img)
	return lab2.WalRecord{PageID: p, Kind: lab2.KindFullPage, Payload: img}
}

// deltaRecord emits 1-4 random spans of 16-128 bytes each.
func deltaRecord(r *rng, p lab2.PageID) lab2.WalRecord {
	n := 1 + r.intn(4)
	spans := make([]lab2.DeltaSpan, n)
	for i := range spans {
		l := 16 + r.intn(113)
		off := r.intn(lab2.PageSize - l)
		data := make([]byte, l)
		r.fill(data)
		spans[i] = lab2.DeltaSpan{Off: uint16(off), Data: data}
	}
	return lab2.WalRecord{PageID: p, Kind: lab2.KindDelta, Payload: lab2.EncodeDeltaPayload(spans)}
}

// rng is a tiny deterministic generator (SplitMix64). We deliberately avoid
// math/rand: its stream is only guaranteed stable per Go release, and the
// conformance vectors must match across toolchains.
type rng struct{ state uint64 }

func newRng(seed int64) *rng { return &rng{state: uint64(seed)} }

func (r *rng) next() uint64 {
	r.state += 0x9e3779b97f4a7c15
	z := r.state
	z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9
	z = (z ^ (z >> 27)) * 0x94d049bb133111eb
	return z ^ (z >> 31)
}

func (r *rng) intn(n int) int { return int(r.next() % uint64(n)) }

func (r *rng) fill(b []byte) {
	for i := 0; i < len(b); i += 8 {
		v := r.next()
		for j := 0; j < 8 && i+j < len(b); j++ {
			b[i+j] = byte(v >> (8 * j))
		}
	}
}
