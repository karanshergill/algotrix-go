package features

import (
	"testing"
	"time"
)

func TestRegistry_RegisterAndCompute(t *testing.T) {
	r := NewRegistry()
	r.Register(FeatureDef{
		Name: "test_feat", Version: 1, Category: "test",
		Trigger: TriggerTick,
		Ready:   func(s *StockState, m *MarketState) bool { return true },
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return s.LTP * 2
		},
	})
	r.buildTriggerIndex()

	s := &StockState{LTP: 100.0}
	m := &MarketState{}

	fv := r.ComputeTriggered(s, m, nil, TriggerTick)
	defer r.ReleaseVector(fv)

	if fv.Values[0] != 200.0 {
		t.Errorf("expected 200.0, got %f", fv.Values[0])
	}
	if !fv.Ready[0] {
		t.Error("expected Ready[0] = true")
	}
	if fv.Version != 1 {
		t.Errorf("expected Version 1, got %d", fv.Version)
	}
}

func TestRegistry_TriggerFiltering(t *testing.T) {
	r := NewRegistry()

	// Tick-triggered feature
	r.Register(FeatureDef{
		Name: "tick_feat", Version: 1, Category: "test",
		Trigger: TriggerTick,
		Ready:   func(s *StockState, m *MarketState) bool { return true },
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return 42.0
		},
	})

	// Depth-triggered feature
	r.Register(FeatureDef{
		Name: "depth_feat", Version: 1, Category: "test",
		Trigger: TriggerDepth,
		Ready:   func(s *StockState, m *MarketState) bool { return true },
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return 99.0
		},
	})
	r.buildTriggerIndex()

	s := &StockState{}
	m := &MarketState{}

	// Compute with TriggerTick — only tick_feat should run
	fv := r.ComputeTriggered(s, m, nil, TriggerTick)
	defer r.ReleaseVector(fv)

	if fv.Values[0] != 42.0 {
		t.Errorf("tick_feat: expected 42.0, got %f", fv.Values[0])
	}
	if !fv.Ready[0] {
		t.Error("tick_feat: expected Ready = true")
	}

	// depth_feat (index 1) should NOT have been computed
	if fv.Ready[1] {
		t.Error("depth_feat should not be ready after TriggerTick")
	}
	if fv.Values[1] != 0 {
		t.Errorf("depth_feat: expected 0, got %f", fv.Values[1])
	}
}

func TestRegistry_ReadyGating(t *testing.T) {
	r := NewRegistry()
	r.Register(FeatureDef{
		Name: "gated_feat", Version: 1, Category: "test",
		Trigger: TriggerTick,
		Ready:   func(s *StockState, m *MarketState) bool { return false },
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return 123.0
		},
	})
	r.buildTriggerIndex()

	s := &StockState{}
	m := &MarketState{}

	fv := r.ComputeTriggered(s, m, nil, TriggerTick)
	defer r.ReleaseVector(fv)

	if fv.Values[0] != 0 {
		t.Errorf("expected 0 (not ready), got %f", fv.Values[0])
	}
	if fv.Ready[0] {
		t.Error("expected Ready[0] = false")
	}
}

func TestRegistry_ToMap(t *testing.T) {
	r := NewRegistry()
	r.Register(FeatureDef{
		Name: "ready_feat", Version: 1, Category: "test",
		Trigger: TriggerTick,
		Ready:   func(s *StockState, m *MarketState) bool { return true },
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return 10.0
		},
	})
	r.Register(FeatureDef{
		Name: "not_ready_feat", Version: 1, Category: "test",
		Trigger: TriggerTick,
		Ready:   func(s *StockState, m *MarketState) bool { return false },
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return 20.0
		},
	})
	r.buildTriggerIndex()

	s := &StockState{}
	m := &MarketState{}

	fv := r.ComputeTriggered(s, m, nil, TriggerTick)
	defer r.ReleaseVector(fv)

	result := r.ToMap(fv)

	if v, ok := result["ready_feat"]; !ok || v != 10.0 {
		t.Errorf("expected ready_feat=10.0 in map, got %v", result)
	}
	if _, ok := result["not_ready_feat"]; ok {
		t.Error("not_ready_feat should not appear in map")
	}
}

func TestTimeToSlot(t *testing.T) {
	cases := []struct {
		hour, min int
		want      int
	}{
		{9, 15, 0},
		{9, 16, 0},
		{9, 19, 0},
		{9, 20, 1},
		{10, 0, 9},
		{15, 25, 74},
		{9, 0, 0},  // pre-9:15 → 0
		{8, 30, 0}, // pre-9:15 → 0
	}
	for _, tc := range cases {
		tm := time.Date(2026, 3, 23, tc.hour, tc.min, 0, 0, time.Local)
		got := timeToSlot(tm)
		if got != tc.want {
			t.Errorf("timeToSlot(%02d:%02d) = %d, want %d", tc.hour, tc.min, got, tc.want)
		}
	}
}

func TestRegistry_FeatureNames(t *testing.T) {
	r := NewDefaultRegistry()
	names := r.FeatureNames()
	if len(names) != 19 {
		t.Errorf("expected 19 features, got %d: %v", len(names), names)
	}

	// Verify expected names are present
	expected := map[string]bool{
		"vwap": true, "vwap_dist_bps": true, "change_pct": true,
		"day_range_pct": true, "exhaustion": true,
		"volume_spike_z": true, "buy_pressure": true,
		"buy_pressure_5m": true, "update_intensity": true,
		"volume_spike_ratio": true, "classified_volume_5m": true,
		"book_imbalance": true, "book_imbalance_weighted": true, "spread_bps": true,
		"breadth_ratio": true, "vwap_breadth": true, "market_buy_pressure": true,
		"sector_breadth": true, "sector_buy_pressure": true,
	}
	for _, name := range names {
		if !expected[name] {
			t.Errorf("unexpected feature name: %s", name)
		}
	}

}
