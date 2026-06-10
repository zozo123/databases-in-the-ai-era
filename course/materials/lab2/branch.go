package lab2

// Branching and garbage collection (Milestones 3 and 4). Bodies are yours;
// signatures and the safety contract in the comments are not.

import (
	"context"
	"fmt"
	"time"
)

// CreateBranch forks a new branch named `name` from `parent` at LSN `at`.
//
// Cost contract (enforced by the grader): O(metadata) — exactly one object
// write (branches/<name>.json), zero page reads, zero page writes,
// regardless of parent size. If this function touches a layer object, your
// design is wrong (Pitfall: "the copy trap").
//
// Steps:
//  1. Load the parent's BranchMeta (authoritative object, not the cache, if
//     you want the freshest flushed head).
//  2. Validate at <= parent's FLUSHED HeadLSN. The in-memory head is not
//     enough: a fork above durable WAL dangles if the parent crashes before
//     its next flush (Checkpoint 2.2 asks you to spell out the wrong read).
//  3. Write the child's BranchMeta: fresh ID (uuid), ParentID = parent.ID,
//     ForkLSN = at, HeadLSN = at, State = live, timestamps = now.
//
// The child's timeline starts EMPTY. Reads below ForkLSN resolve through the
// ancestor chain (GetPage step 3); writes append to the child only.
func (ps *Pageserver) CreateBranch(ctx context.Context, name, parent string, at LSN) (*BranchMeta, error) {
	// TODO(M3)
	return nil, fmt.Errorf("CreateBranch: not implemented")
}

// DeleteBranch marks a branch dead. It does NOT delete any layer objects —
// layers are shared down the ancestry tree, and reclamation is GC's job
// (with a real safety argument, not a TTL).
func (ps *Pageserver) DeleteBranch(ctx context.Context, name string) error {
	// TODO(M4): flip State to dead in branches/<name>.json. Think about a
	// dead branch that still has live descendants: its fork points remain
	// load-bearing for THEM.
	return fmt.Errorf("DeleteBranch: not implemented")
}

// GCReport is what RunGC returns and what `make swarm` prints.
type GCReport struct {
	LayersExamined    int
	LayersDeleted     int
	BytesReclaimed    int64
	BranchesCollapsed int // dead/abandoned branches whose metadata was removed
	GraceEpoch        time.Duration
}

// RunGC reclaims layers (and dead-branch metadata) that no live read can
// ever need. Two-phase mark-and-sweep:
//
// MARK — against a SNAPSHOT of authoritative branch metadata:
//  1. Compute the live set of branches: State == live, plus your abandonment
//     policy (e.g. live but LastReadAt older than `idle` counts as dead —
//     choose a policy and defend it in the report).
//  2. Compute, per timeline, the set of LSNs that must remain readable:
//     every live branch's head, minus the PITR horizon `horizon`, plus the
//     fork_lsn of every live descendant (a layer entirely older than every
//     head can still be the only path to pages below a fork — Pitfall "GC
//     below a fork", Checkpoint 2.3).
//  3. A layer is a candidate iff, for every live branch whose ancestry
//     passes through its timeline, every readable LSN that could resolve
//     through it is already covered by a newer image layer at or below it.
//
// GRACE — wait out a grace epoch before deleting: an in-flight GetPage may
// hold a layer map snapshot from before the mark. (Checkpoint 2.3: the grace
// epoch is required even when the invariant is evaluated correctly — what
// failure does it prevent?)
//
// SWEEP — DELETE candidates, update the manifest, then remove metadata of
// collapsed branches. Order the manifest update vs. the DELETEs so a crash
// mid-sweep leaves garbage (safe, re-collectable), never dangling references.
func (ps *Pageserver) RunGC(ctx context.Context, horizon, idle, grace time.Duration) (GCReport, error) {
	// TODO(M4)
	return GCReport{}, fmt.Errorf("RunGC: not implemented")
}
