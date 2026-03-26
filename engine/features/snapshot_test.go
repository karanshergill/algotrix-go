package features

import (
	"context"
	"testing"
	"time"
)

func TestEngineSnapshot_Clone(t *testing.T) {
	orig := NewEngineSnapshot()
	orig.Stocks["ISIN1"] = StockSnapshot{
		ISIN: "ISIN1", Symbol: "SYM1", LTP: 100.0,
		Features: map[string]float64{"vwap": 99.5},
		Quality:  QualityFlags{Partial: true},
	}
	orig.Market = MarketSnapshot{StocksUp: 5, TotalStocks: 10}
	orig.Sectors["BANK"] = SectorSnapshot{Name: "BANK", StocksUp: 3}
	orig.TS = time.Now()

	cloned := orig.Clone()

	// Modify original after clone
	orig.Stocks["ISIN2"] = StockSnapshot{ISIN: "ISIN2", LTP: 200.0}
	orig.Market.StocksUp = 99
	orig.Sectors["IT"] = SectorSnapshot{Name: "IT"}

	// Verify clone is unaffected
	if _, ok := cloned.Stocks["ISIN2"]; ok {
		t.Error("clone should not have ISIN2")
	}
	if cloned.Market.StocksUp != 5 {
		t.Errorf("clone Market.StocksUp should be 5, got %d", cloned.Market.StocksUp)
	}
	if _, ok := cloned.Sectors["IT"]; ok {
		t.Error("clone should not have IT sector")
	}

	// Verify clone has original data
	s1, ok := cloned.Stocks["ISIN1"]
	if !ok {
		t.Fatal("clone missing ISIN1")
	}
	if s1.LTP != 100.0 {
		t.Errorf("expected LTP 100.0, got %f", s1.LTP)
	}
	if s1.Features["vwap"] != 99.5 {
		t.Errorf("expected vwap 99.5, got %f", s1.Features["vwap"])
	}

	// Verify Features map is a deep copy
	orig.Stocks["ISIN1"] = StockSnapshot{
		Features: map[string]float64{"vwap": 999.0},
	}
	if cloned.Stocks["ISIN1"].Features["vwap"] != 99.5 {
		t.Error("clone Features map was not deep copied")
	}
}

func TestEngineSnapshot_UpdateStock(t *testing.T) {
	snap := NewEngineSnapshot()
	snap.Stocks["A"] = StockSnapshot{ISIN: "A", LTP: 10.0}
	snap.Stocks["B"] = StockSnapshot{ISIN: "B", LTP: 20.0}

	snap.UpdateStock("A", StockSnapshot{ISIN: "A", LTP: 15.0})

	if snap.Stocks["A"].LTP != 15.0 {
		t.Errorf("expected A LTP 15.0, got %f", snap.Stocks["A"].LTP)
	}
	if snap.Stocks["B"].LTP != 20.0 {
		t.Errorf("expected B LTP unchanged at 20.0, got %f", snap.Stocks["B"].LTP)
	}
}

func TestFeatureEngine_SnapshotUpdated(t *testing.T) {
	e := NewFeatureEngine(nil)
	e.SetSyncSnapshot(true)
	e.RegisterStock("ISIN1", "SYM1", "")
	e.session.SessionStart(time.Now())

	// Verify initial snapshot is empty
	snap := e.Snapshot()
	if len(snap.Stocks) != 0 {
		t.Fatalf("expected empty stocks, got %d", len(snap.Stocks))
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	done := make(chan struct{})
	e.SetOnTick(func(isin string) {
		close(done)
	})

	go e.Run(ctx)

	e.TickChan() <- TickEvent{
		ISIN:   "ISIN1",
		Symbol: "SYM1",
		LTP:    150.0,
		Volume: 1000,
		TS:     time.Now(),
	}

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("timeout waiting for tick")
	}

	snap = e.Snapshot()
	ss, ok := snap.Stocks["ISIN1"]
	if !ok {
		t.Fatal("snapshot missing ISIN1 after tick")
	}
	if ss.LTP != 150.0 {
		t.Errorf("expected LTP 150.0, got %f", ss.LTP)
	}
	if ss.Symbol != "SYM1" {
		t.Errorf("expected symbol SYM1, got %s", ss.Symbol)
	}
	if !ss.Quality.Partial {
		t.Error("expected Partial=true after 1 tick")
	}
	if snap.TS.IsZero() {
		t.Error("expected non-zero snapshot timestamp")
	}
}
