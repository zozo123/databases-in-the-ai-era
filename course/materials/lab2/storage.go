package lab2

// This file is COMPLETE. It gives you a thin object-store abstraction with
// two implementations: an in-memory fake for unit tests (with GET/PUT
// counters, because the grader counts GETs and so should you) and a MinIO
// implementation for the real thing.
//
// Deliberately minimal: Put / Get / GetRange / List / Delete. If you find
// yourself wanting more verbs, you are probably about to violate the
// "LIST is not a catalog" rule — re-read the handout first.

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"
	"sync"
	"sync/atomic"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

// ErrNotFound is returned by Get/GetRange for a missing key, by every
// implementation. Test with errors.Is.
var ErrNotFound = errors.New("object not found")

// ObjectStore is everything your pageserver may assume about the bucket.
// All objects are immutable once written (Put of an existing key is allowed
// only for branch metadata, which is small and atomic on S3/MinIO).
type ObjectStore interface {
	// Put writes data under key, replacing any existing object atomically.
	Put(ctx context.Context, key string, data []byte) error
	// Get returns the whole object, or ErrNotFound.
	Get(ctx context.Context, key string) ([]byte, error)
	// GetRange returns length bytes starting at off — your single-GET point
	// lookups into indexed image layers go through this.
	GetRange(ctx context.Context, key string, off, length int64) ([]byte, error)
	// List returns all keys with the given prefix, sorted. For debugging and
	// GC sweep ONLY — never as the authority for what exists.
	List(ctx context.Context, prefix string) ([]string, error)
	// Delete removes key; deleting a missing key is not an error.
	Delete(ctx context.Context, key string) error
}

// ---------------------------------------------------------------------------
// In-memory fake (for tests)
// ---------------------------------------------------------------------------

// MemStore is a thread-safe in-memory ObjectStore. Gets/Puts counters let
// your tests assert the layers-touched bound from Milestone 2.
type MemStore struct {
	mu      sync.RWMutex
	objects map[string][]byte

	Gets atomic.Int64 // Get + GetRange calls
	Puts atomic.Int64
}

func NewMemStore() *MemStore {
	return &MemStore{objects: make(map[string][]byte)}
}

func (s *MemStore) Put(_ context.Context, key string, data []byte) error {
	s.Puts.Add(1)
	s.mu.Lock()
	defer s.mu.Unlock()
	s.objects[key] = append([]byte(nil), data...)
	return nil
}

func (s *MemStore) Get(_ context.Context, key string) ([]byte, error) {
	s.Gets.Add(1)
	s.mu.RLock()
	defer s.mu.RUnlock()
	data, ok := s.objects[key]
	if !ok {
		return nil, fmt.Errorf("%w: %s", ErrNotFound, key)
	}
	return append([]byte(nil), data...), nil
}

func (s *MemStore) GetRange(_ context.Context, key string, off, length int64) ([]byte, error) {
	s.Gets.Add(1)
	s.mu.RLock()
	defer s.mu.RUnlock()
	data, ok := s.objects[key]
	if !ok {
		return nil, fmt.Errorf("%w: %s", ErrNotFound, key)
	}
	if off < 0 || off > int64(len(data)) {
		return nil, fmt.Errorf("range [%d,+%d) out of bounds for %s (%d bytes)", off, length, key, len(data))
	}
	end := off + length
	if end > int64(len(data)) {
		end = int64(len(data))
	}
	return append([]byte(nil), data[off:end]...), nil
}

func (s *MemStore) List(_ context.Context, prefix string) ([]string, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var keys []string
	for k := range s.objects {
		if strings.HasPrefix(k, prefix) {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	return keys, nil
}

func (s *MemStore) Delete(_ context.Context, key string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.objects, key)
	return nil
}

// ---------------------------------------------------------------------------
// MinIO / S3 implementation
// ---------------------------------------------------------------------------

// MinIOStore is an ObjectStore backed by a MinIO (or any S3-compatible)
// bucket. See README.md for the docker one-liner that starts MinIO locally.
type MinIOStore struct {
	client *minio.Client
	bucket string
}

// NewMinIOStore connects to endpoint (e.g. "localhost:9000") and creates the
// bucket if it does not exist yet.
func NewMinIOStore(ctx context.Context, endpoint, accessKey, secretKey, bucket string, useSSL bool) (*MinIOStore, error) {
	client, err := minio.New(endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(accessKey, secretKey, ""),
		Secure: useSSL,
	})
	if err != nil {
		return nil, fmt.Errorf("minio connect %s: %w", endpoint, err)
	}
	exists, err := client.BucketExists(ctx, bucket)
	if err != nil {
		return nil, fmt.Errorf("minio bucket-exists %s: %w", bucket, err)
	}
	if !exists {
		if err := client.MakeBucket(ctx, bucket, minio.MakeBucketOptions{}); err != nil {
			return nil, fmt.Errorf("minio make-bucket %s: %w", bucket, err)
		}
	}
	return &MinIOStore{client: client, bucket: bucket}, nil
}

func (s *MinIOStore) Put(ctx context.Context, key string, data []byte) error {
	_, err := s.client.PutObject(ctx, s.bucket, key, bytes.NewReader(data), int64(len(data)),
		minio.PutObjectOptions{ContentType: "application/octet-stream"})
	if err != nil {
		return fmt.Errorf("put %s: %w", key, err)
	}
	return nil
}

func (s *MinIOStore) Get(ctx context.Context, key string) ([]byte, error) {
	obj, err := s.client.GetObject(ctx, s.bucket, key, minio.GetObjectOptions{})
	if err != nil {
		return nil, fmt.Errorf("get %s: %w", key, err)
	}
	defer obj.Close()
	data, err := io.ReadAll(obj)
	if err != nil {
		return nil, s.wrapErr(key, err)
	}
	return data, nil
}

func (s *MinIOStore) GetRange(ctx context.Context, key string, off, length int64) ([]byte, error) {
	opts := minio.GetObjectOptions{}
	if err := opts.SetRange(off, off+length-1); err != nil {
		return nil, fmt.Errorf("get-range %s: %w", key, err)
	}
	obj, err := s.client.GetObject(ctx, s.bucket, key, opts)
	if err != nil {
		return nil, fmt.Errorf("get-range %s: %w", key, err)
	}
	defer obj.Close()
	data, err := io.ReadAll(obj)
	if err != nil {
		return nil, s.wrapErr(key, err)
	}
	return data, nil
}

func (s *MinIOStore) List(ctx context.Context, prefix string) ([]string, error) {
	var keys []string
	for obj := range s.client.ListObjects(ctx, s.bucket, minio.ListObjectsOptions{Prefix: prefix, Recursive: true}) {
		if obj.Err != nil {
			return nil, fmt.Errorf("list %s: %w", prefix, obj.Err)
		}
		keys = append(keys, obj.Key)
	}
	sort.Strings(keys)
	return keys, nil
}

func (s *MinIOStore) Delete(ctx context.Context, key string) error {
	if err := s.client.RemoveObject(ctx, s.bucket, key, minio.RemoveObjectOptions{}); err != nil {
		return fmt.Errorf("delete %s: %w", key, err)
	}
	return nil
}

// wrapErr converts MinIO's NoSuchKey into ErrNotFound so callers can use
// errors.Is regardless of backend.
func (s *MinIOStore) wrapErr(key string, err error) error {
	resp := minio.ToErrorResponse(err)
	if resp.Code == "NoSuchKey" {
		return fmt.Errorf("%w: %s", ErrNotFound, key)
	}
	return fmt.Errorf("read %s: %w", key, err)
}
