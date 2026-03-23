package features

import (
	"testing"
	"time"
)

var testIST = time.FixedZone("IST", 5*3600+30*60)

func makeTestEngine() *FeatureEngine {
	engine := NewFeatureEngine(DefaultConfig())
	engine.RegisterStock("INE001", "RELIANCE", "NIFTY_ENERGY")
	engine.RegisterStock("INE002", "TCS", "NIFTY_IT")
	engine.RegisterSector("NIFTY_ENERGY", []string{"INE001"})
	engine.RegisterSector("NIFTY_IT", []string{"INE002"})

	// Set baselines so features have meaningful values
	s1 := engine.Stock("INE001")
	s1.PrevClose = 2400.0
	s1.ATR14d = 50.0
	s1.VolumeSlot = map[int]VolumeSlotBaseline{
		0: {Mean: 10000, StdDev: 2000, Samples: 10},
		1: {Mean: 8000, StdDev: 1500, Samples: 10},
	}

	s2 := engine.Stock("INE002")
	s2.PrevClose = 3500.0
	s2.ATR14d = 40.0
	s2.VolumeSlot = map[int]VolumeSlotBaseline{
		0: {Mean: 5000, StdDev: 1000, Samples: 10},
	}
	return engine
}

func makeTestTicks() []TickEvent {
	base := time.Date(2026, 3, 12, 9, 15, 0, 0, testIST)
	return []TickEvent{
		{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2420.0, Volume: 5000, TS: base},
		{ISIN: "INE002", Symbol: "TCS", LTP: 3520.0, Volume: 3000, TS: base.Add(1 * time.Second)},
		{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2430.0, Volume: 12000, TS: base.Add(5 * time.Second)},
		{ISIN: "INE002", Symbol: "TCS", LTP: 3510.0, Volume: 7000, TS: base.Add(6 * time.Second)},
		{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2425.0, Volume: 18000, TS: base.Add(10 * time.Second)},
		{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2440.0, Volume: 25000, TS: base.Add(15 * time.Second)},
		{ISIN: "INE002", Symbol: "TCS", LTP: 3530.0, Volume: 12000, TS: base.Add(16 * time.Second)},
		{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2435.0, Volume: 30000, TS: base.Add(20 * time.Second)},
	}
}

func TestReplayTicks_NonZeroFeatures(t *testing.T) {
	engine := makeTestEngine()
	ticks := makeTestTicks()

	result := replayTicks(engine, ticks)

	// Both stocks should appear
	if len(result) != 2 {
		t.Fatalf("expected 2 stocks in result, got %d", len(result))
	}

	for isin, features := range result {
		if len(features) == 0 {
			t.Errorf("%s: no features computed", isin)
			continue
		}

		// At least some features should be non-zero
		nonZero := 0
		for _, v := range features {
			if v != 0 {
				nonZero++
			}
		}
		if nonZero == 0 {
			t.Errorf("%s: all features are zero", isin)
		}
	}

	// Specific feature checks for INE001 (RELIANCE)
	rel := result["INE001"]
	if rel["vwap"] == 0 {
		t.Error("INE001 vwap should be non-zero")
	}
	if rel["change_pct"] == 0 {
		t.Error("INE001 change_pct should be non-zero")
	}
	// change_pct should be positive (2435 vs prev 2400)
	if rel["change_pct"] <= 0 {
		t.Errorf("INE001 change_pct = %f, expected positive", rel["change_pct"])
	}
}

func TestReplayTicks_Deterministic(t *testing.T) {
	r1 := replayTicks(makeTestEngine(), makeTestTicks())
	r2 := replayTicks(makeTestEngine(), makeTestTicks())

	// Both runs must have the same ISINs
	if len(r1) != len(r2) {
		t.Fatalf("run1 has %d stocks, run2 has %d", len(r1), len(r2))
	}

	for isin, f1 := range r1 {
		f2, ok := r2[isin]
		if !ok {
			t.Errorf("ISIN %s in run1 but not run2", isin)
			continue
		}
		for name, v1 := range f1 {
			v2, ok := f2[name]
			if !ok {
				t.Errorf("%s: feature %s in run1 but not run2", isin, name)
				continue
			}
			if v1 != v2 {
				t.Errorf("%s/%s: run1=%f, run2=%f", isin, name, v1, v2)
			}
		}
		// Check no extra features in run2
		for name := range f2 {
			if _, ok := f1[name]; !ok {
				t.Errorf("%s: feature %s in run2 but not run1", isin, name)
			}
		}
	}
}

func TestReplayTicks_EmptyInput(t *testing.T) {
	engine := makeTestEngine()
	result := replayTicks(engine, nil)
	if result != nil {
		t.Errorf("expected nil for empty input, got %v", result)
	}
}

func TestReplayTicks_SingleTick(t *testing.T) {
	engine := makeTestEngine()
	base := time.Date(2026, 3, 12, 9, 15, 0, 0, testIST)
	ticks := []TickEvent{
		{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2420.0, Volume: 5000, TS: base},
	}

	result := replayTicks(engine, ticks)
	if len(result) != 1 {
		t.Fatalf("expected 1 stock, got %d", len(result))
	}
	if _, ok := result["INE001"]; !ok {
		t.Fatal("INE001 missing from result")
	}
}
