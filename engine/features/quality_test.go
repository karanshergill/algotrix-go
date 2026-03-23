package features

import (
	"testing"
	"time"
)

func TestQualityFlags_Partial(t *testing.T) {
	s := &StockState{
		UpdateCount: 10,
		ATR14d:      1.5,
		VolumeSlot:  map[int]VolumeSlotBaseline{0: {Mean: 100}},
		LastTickTS:  time.Now().Add(-1 * time.Second),
		LastDepthTS: time.Now().Add(-1 * time.Second),
	}
	q := ComputeQuality(s, time.Now())
	if !q.Partial {
		t.Error("expected Partial=true for UpdateCount < 20")
	}
	if q.BaselineMissing {
		t.Error("expected BaselineMissing=false when ATR14d and VolumeSlot set")
	}
}

func TestQualityFlags_BaselineMissing(t *testing.T) {
	s := &StockState{
		UpdateCount: 50,
		ATR14d:      0, // missing
		VolumeSlot:  map[int]VolumeSlotBaseline{0: {Mean: 100}},
		LastTickTS:  time.Now().Add(-1 * time.Second),
		LastDepthTS: time.Now().Add(-1 * time.Second),
	}
	q := ComputeQuality(s, time.Now())
	if !q.BaselineMissing {
		t.Error("expected BaselineMissing=true when ATR14d == 0")
	}
	if q.Partial {
		t.Error("expected Partial=false for UpdateCount >= 20")
	}

	// Also test empty VolumeSlot
	s2 := &StockState{
		UpdateCount: 50,
		ATR14d:      1.5,
		VolumeSlot:  nil,
	}
	q2 := ComputeQuality(s2, time.Now())
	if !q2.BaselineMissing {
		t.Error("expected BaselineMissing=true when VolumeSlot is nil")
	}
}

func TestQualityFlags_Normal(t *testing.T) {
	now := time.Now()
	s := &StockState{
		UpdateCount: 100,
		ATR14d:      2.5,
		VolumeSlot:  map[int]VolumeSlotBaseline{0: {Mean: 100}},
		LastTickTS:  now.Add(-500 * time.Millisecond),
		LastDepthTS: now.Add(-200 * time.Millisecond),
	}
	q := ComputeQuality(s, now)
	if q.Partial {
		t.Error("expected Partial=false")
	}
	if q.BaselineMissing {
		t.Error("expected BaselineMissing=false")
	}
	if q.TickStaleMs < 400 || q.TickStaleMs > 700 {
		t.Errorf("expected TickStaleMs ~500, got %d", q.TickStaleMs)
	}
	if q.DepthStaleMs < 100 || q.DepthStaleMs > 400 {
		t.Errorf("expected DepthStaleMs ~200, got %d", q.DepthStaleMs)
	}
}

func TestQualityFlags_ZeroTimestamps(t *testing.T) {
	s := &StockState{UpdateCount: 5}
	q := ComputeQuality(s, time.Now())
	if q.DepthStaleMs != 0 {
		t.Errorf("expected DepthStaleMs=0 for zero timestamp, got %d", q.DepthStaleMs)
	}
	if q.TickStaleMs != 0 {
		t.Errorf("expected TickStaleMs=0 for zero timestamp, got %d", q.TickStaleMs)
	}
}
