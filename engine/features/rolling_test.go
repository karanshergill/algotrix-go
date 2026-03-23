package features

import (
	"testing"
	"time"
)

func TestRollingSum_BasicAddAndEvict(t *testing.T) {
	rs := NewRollingSum(5*time.Second, 100)
	now := time.Date(2026, 1, 1, 9, 15, 0, 0, time.UTC)

	rs.Add(now, 10)
	rs.Add(now.Add(1*time.Second), 20)
	rs.Add(now.Add(2*time.Second), 30)

	if got := rs.Sum(); got != 60 {
		t.Errorf("Sum = %d, want 60", got)
	}
	if got := rs.Count(); got != 3 {
		t.Errorf("Count = %d, want 3", got)
	}

	// Add entry at now+6s — first entry (at now) should be evicted (older than 5s window)
	rs.Add(now.Add(6*time.Second), 40)

	if got := rs.Sum(); got != 90 {
		t.Errorf("after eviction: Sum = %d, want 90 (20+30+40)", got)
	}
	if got := rs.Count(); got != 3 {
		t.Errorf("after eviction: Count = %d, want 3", got)
	}

	// Add at now+8s — entries at now+1s and now+2s should also be evicted
	rs.Add(now.Add(8*time.Second), 50)

	if got := rs.Sum(); got != 90 {
		t.Errorf("after second eviction: Sum = %d, want 90 (40+50)", got)
	}
	if got := rs.Count(); got != 2 {
		t.Errorf("after second eviction: Count = %d, want 2", got)
	}
}

func TestRollingSum_CircularBuffer(t *testing.T) {
	// Small capacity to force wrapping
	rs := NewRollingSum(10*time.Second, 4)
	now := time.Date(2026, 1, 1, 9, 15, 0, 0, time.UTC)

	// Fill buffer to capacity
	rs.Add(now, 1)
	rs.Add(now.Add(1*time.Second), 2)
	rs.Add(now.Add(2*time.Second), 3)
	rs.Add(now.Add(3*time.Second), 4)

	if got := rs.Sum(); got != 10 {
		t.Errorf("full buffer: Sum = %d, want 10", got)
	}
	if got := rs.Count(); got != 4 {
		t.Errorf("full buffer: Count = %d, want 4", got)
	}

	// Add at now+11s — first entry evicted, wraps around
	rs.Add(now.Add(11*time.Second), 5)

	if got := rs.Sum(); got != 14 {
		t.Errorf("after wrap: Sum = %d, want 14 (2+3+4+5)", got)
	}

	// Add more to keep wrapping
	rs.Add(now.Add(12*time.Second), 6)

	if got := rs.Sum(); got != 18 {
		t.Errorf("after second wrap: Sum = %d, want 18 (3+4+5+6)", got)
	}
}

func TestRollingSum_Reset(t *testing.T) {
	rs := NewRollingSum(5*time.Second, 100)
	now := time.Date(2026, 1, 1, 9, 15, 0, 0, time.UTC)

	rs.Add(now, 10)
	rs.Add(now.Add(1*time.Second), 20)
	rs.Reset()

	if got := rs.Sum(); got != 0 {
		t.Errorf("after reset: Sum = %d, want 0", got)
	}
	if got := rs.Count(); got != 0 {
		t.Errorf("after reset: Count = %d, want 0", got)
	}

	// Should work normally after reset
	rs.Add(now.Add(2*time.Second), 5)
	if got := rs.Sum(); got != 5 {
		t.Errorf("after reset+add: Sum = %d, want 5", got)
	}
}

func TestRollingExtreme_Max(t *testing.T) {
	re := NewRollingExtreme(5*time.Second, true)
	now := time.Date(2026, 1, 1, 9, 15, 0, 0, time.UTC)

	re.Add(now, 100.0)
	re.Add(now.Add(1*time.Second), 120.0)
	re.Add(now.Add(2*time.Second), 110.0)

	if got := re.Value(); got != 120.0 {
		t.Errorf("Max = %f, want 120.0", got)
	}

	// Add at now+6s — entry at now is evicted, but max (120 at now+1s) is still in window
	re.Add(now.Add(6*time.Second), 105.0)

	if got := re.Value(); got != 120.0 {
		t.Errorf("after partial eviction: Max = %f, want 120.0", got)
	}

	// Add at now+7s — entry at now+1s (the 120) is now evicted
	re.Add(now.Add(7*time.Second), 108.0)

	if got := re.Value(); got != 110.0 {
		t.Errorf("after max eviction: Max = %f, want 110.0", got)
	}
}

func TestRollingExtreme_Min(t *testing.T) {
	re := NewRollingExtreme(5*time.Second, false)
	now := time.Date(2026, 1, 1, 9, 15, 0, 0, time.UTC)

	re.Add(now, 100.0)
	re.Add(now.Add(1*time.Second), 80.0)
	re.Add(now.Add(2*time.Second), 90.0)

	if got := re.Value(); got != 80.0 {
		t.Errorf("Min = %f, want 80.0", got)
	}

	// Add at now+6s — entry at now evicted, min (80 at now+1s) still in window
	re.Add(now.Add(6*time.Second), 95.0)

	if got := re.Value(); got != 80.0 {
		t.Errorf("after partial eviction: Min = %f, want 80.0", got)
	}

	// Add at now+7s — entry at now+1s (the 80) evicted
	re.Add(now.Add(7*time.Second), 92.0)

	if got := re.Value(); got != 90.0 {
		t.Errorf("after min eviction: Min = %f, want 90.0", got)
	}
}

func TestRollingExtreme_Reset(t *testing.T) {
	re := NewRollingExtreme(5*time.Second, true)
	now := time.Date(2026, 1, 1, 9, 15, 0, 0, time.UTC)

	re.Add(now, 100.0)
	re.Add(now.Add(1*time.Second), 200.0)
	re.Reset()

	if got := re.Value(); got != 0 {
		t.Errorf("after reset: Value = %f, want 0", got)
	}

	// Should work normally after reset
	re.Add(now.Add(2*time.Second), 50.0)
	if got := re.Value(); got != 50.0 {
		t.Errorf("after reset+add: Value = %f, want 50.0", got)
	}
}
