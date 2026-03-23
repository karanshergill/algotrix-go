package features

import "time"

// ---------------------------------------------------------------------------
// tsEntry is a timestamped int64 value for RollingSum's circular buffer.
// ---------------------------------------------------------------------------

type tsEntry struct {
	ts  time.Time
	val int64
}

// ---------------------------------------------------------------------------
// RollingSum maintains a time-windowed sum using a pre-allocated circular buffer.
// ---------------------------------------------------------------------------

type RollingSum struct {
	window time.Duration
	buf    []tsEntry
	cap    int
	head   int
	count  int
	sum    int64
}

// NewRollingSum creates a RollingSum with the given time window and buffer capacity.
func NewRollingSum(window time.Duration, capacity int) *RollingSum {
	return &RollingSum{
		window: window,
		buf:    make([]tsEntry, capacity),
		cap:    capacity,
	}
}

// Add inserts a new timestamped value, evicting entries older than the window.
func (r *RollingSum) Add(ts time.Time, val int64) {
	r.evict(ts)
	idx := (r.head + r.count) % r.cap
	r.buf[idx] = tsEntry{ts, val}
	r.count++
	r.sum += val
}

// evict removes entries from the head that are older than the window.
func (r *RollingSum) evict(now time.Time) {
	cutoff := now.Add(-r.window)
	for r.count > 0 && r.buf[r.head].ts.Before(cutoff) {
		r.sum -= r.buf[r.head].val
		r.head = (r.head + 1) % r.cap
		r.count--
	}
}

// Sum returns the current windowed sum.
func (r *RollingSum) Sum() int64 { return r.sum }

// Count returns the number of entries currently in the window.
func (r *RollingSum) Count() int { return r.count }

// Reset clears all entries, keeping the allocated buffer.
func (r *RollingSum) Reset() {
	r.head = 0
	r.count = 0
	r.sum = 0
}

// ---------------------------------------------------------------------------
// tsFloat is a timestamped float64 value for RollingExtreme's monotonic deque.
// ---------------------------------------------------------------------------

type tsFloat struct {
	ts  time.Time
	val float64
}

// ---------------------------------------------------------------------------
// RollingExtreme tracks a rolling max or min over a time window using a
// monotonic deque for O(1) queries.
// ---------------------------------------------------------------------------

type RollingExtreme struct {
	window time.Duration
	isMax  bool
	deque  []tsFloat
}

// NewRollingExtreme creates a RollingExtreme. isMax=true tracks max, false tracks min.
func NewRollingExtreme(window time.Duration, isMax bool) *RollingExtreme {
	return &RollingExtreme{
		window: window,
		isMax:  isMax,
	}
}

// Add inserts a new timestamped value, maintaining the monotonic deque invariant.
func (r *RollingExtreme) Add(ts time.Time, val float64) {
	// Evict expired entries from front
	cutoff := ts.Add(-r.window)
	for len(r.deque) > 0 && r.deque[0].ts.Before(cutoff) {
		r.deque = r.deque[1:]
	}
	// Maintain monotonicity from back
	if r.isMax {
		for len(r.deque) > 0 && r.deque[len(r.deque)-1].val <= val {
			r.deque = r.deque[:len(r.deque)-1]
		}
	} else {
		for len(r.deque) > 0 && r.deque[len(r.deque)-1].val >= val {
			r.deque = r.deque[:len(r.deque)-1]
		}
	}
	r.deque = append(r.deque, tsFloat{ts, val})
}

// Value returns the current extreme (max or min). Returns 0 if empty.
func (r *RollingExtreme) Value() float64 {
	if len(r.deque) == 0 {
		return 0
	}
	return r.deque[0].val
}

// Reset clears the deque.
func (r *RollingExtreme) Reset() {
	r.deque = r.deque[:0]
}
