package features

import (
	"context"
	"testing"
	"time"
)

// helper: create engine, register stock, start session, wire onTick callback
func setupEngine(t *testing.T) (*FeatureEngine, context.CancelFunc, chan string) {
	t.Helper()
	e := NewFeatureEngine(DefaultEngineConfig())
	e.RegisterStock("INE001", "RELIANCE", "NIFTY_50")

	// Start session so ticks are accepted
	e.Session().SessionStart(time.Now())

	done := make(chan string, 16)
	e.SetOnTick(func(isin string) { done <- isin })

	ctx, cancel := context.WithCancel(context.Background())
	go e.Run(ctx)

	return e, cancel, done
}

func waitFor(t *testing.T, ch <-chan string, timeout time.Duration) string {
	t.Helper()
	select {
	case isin := <-ch:
		return isin
	case <-time.After(timeout):
		t.Fatal("timed out waiting for callback")
		return ""
	}
}

func TestFeatureEngine_TickProcessing(t *testing.T) {
	e, cancel, done := setupEngine(t)
	defer cancel()

	ts := time.Now()
	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2500.0, Volume: 1000, TS: ts}
	waitFor(t, done, 2*time.Second)

	s := e.Stock("INE001")
	if s.LTP != 2500.0 {
		t.Errorf("LTP = %f, want 2500.0", s.LTP)
	}
	if s.CumulativeVolume != 1000 {
		t.Errorf("CumulativeVolume = %d, want 1000", s.CumulativeVolume)
	}
	if s.DayOpen != 2500.0 {
		t.Errorf("DayOpen = %f, want 2500.0", s.DayOpen)
	}
	if s.DayHigh != 2500.0 {
		t.Errorf("DayHigh = %f, want 2500.0", s.DayHigh)
	}
	if s.DayLow != 2500.0 {
		t.Errorf("DayLow = %f, want 2500.0", s.DayLow)
	}
}

func TestFeatureEngine_DepthProcessing(t *testing.T) {
	e := NewFeatureEngine(DefaultEngineConfig())
	e.RegisterStock("INE001", "RELIANCE", "NIFTY_50")
	e.Session().SessionStart(time.Now())

	depthDone := make(chan string, 16)
	e.SetOnDepth(func(isin string) { depthDone <- isin })

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go e.Run(ctx)

	ts := time.Now()
	e.DepthChan() <- DepthEvent{
		ISIN: "INE001",
		Bids: []DepthLevel{{Price: 2499.0, Qty: 100}, {Price: 2498.0, Qty: 200}},
		Asks: []DepthLevel{{Price: 2501.0, Qty: 150}, {Price: 2502.0, Qty: 250}},
		TS:   ts,
	}
	waitFor(t, depthDone, 2*time.Second)

	s := e.Stock("INE001")
	if !s.HasDepth {
		t.Error("HasDepth should be true")
	}
	if s.BidPrices[0] != 2499.0 {
		t.Errorf("BidPrices[0] = %f, want 2499.0", s.BidPrices[0])
	}
	if s.AskPrices[0] != 2501.0 {
		t.Errorf("AskPrices[0] = %f, want 2501.0", s.AskPrices[0])
	}
	if s.TotalBidQty != 300 {
		t.Errorf("TotalBidQty = %d, want 300", s.TotalBidQty)
	}
	if s.TotalAskQty != 400 {
		t.Errorf("TotalAskQty = %d, want 400", s.TotalAskQty)
	}
}

func TestFeatureEngine_SessionGate(t *testing.T) {
	e := NewFeatureEngine(DefaultEngineConfig())
	e.RegisterStock("INE001", "RELIANCE", "NIFTY_50")
	// Do NOT call SessionStart — session is in PreOpen state

	tickFired := make(chan string, 16)
	e.SetOnTick(func(isin string) { tickFired <- isin })

	// Phase 1: Run in PreOpen, send a tick (will be rejected), then stop Run.
	ctx1, cancel1 := context.WithCancel(context.Background())
	runDone := make(chan struct{})
	go func() {
		e.Run(ctx1)
		close(runDone)
	}()

	ts := time.Now()
	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2500.0, Volume: 1000, TS: ts}

	// Stop the event loop and wait for it to exit — ensures no concurrent access.
	cancel1()
	<-runDone

	// Drain any unconsumed tick from the channel (may or may not have been consumed).
	select {
	case <-e.tickCh:
	default:
	}

	s := e.Stock("INE001")
	if s.LTP != 0 {
		t.Errorf("LTP = %f, want 0 (tick should have been rejected in PreOpen)", s.LTP)
	}

	// Phase 2: Start session (safe — no concurrent goroutine), restart Run.
	e.Session().SessionStart(time.Now())

	ctx2, cancel2 := context.WithCancel(context.Background())
	defer cancel2()
	go e.Run(ctx2)

	ts2 := ts.Add(time.Second)
	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2600.0, Volume: 2000, TS: ts2}
	waitFor(t, tickFired, 2*time.Second)

	if s.LTP != 2600.0 {
		t.Errorf("LTP = %f, want 2600.0", s.LTP)
	}
	if s.CumulativeVolume != 2000 {
		t.Errorf("CumulativeVolume = %d, want 2000", s.CumulativeVolume)
	}
}

func TestFeatureEngine_GuardRejectsBadTick(t *testing.T) {
	e, cancel, done := setupEngine(t)
	defer cancel()

	ts := time.Now()
	// Send tick with LTP=0 — should be rejected by guard
	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: 0, Volume: 1000, TS: ts}

	// Send a valid tick to confirm engine is responsive
	ts2 := ts.Add(time.Second)
	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2500.0, Volume: 1000, TS: ts2}
	waitFor(t, done, 2*time.Second)

	s := e.Stock("INE001")
	if s.LTP != 2500.0 {
		t.Errorf("LTP = %f, want 2500.0 (zero-LTP tick should have been rejected)", s.LTP)
	}
}

func TestFeatureEngine_VolumeClassification(t *testing.T) {
	e, cancel, done := setupEngine(t)
	defer cancel()

	ts := time.Now()

	// Tick 1: initial price 100, volume 1000
	e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 100.0, Volume: 1000, TS: ts}
	waitFor(t, done, 2*time.Second)

	// Tick 2: price UP to 105, volume 2000 → delta 1000 classified as BUY
	ts = ts.Add(time.Second)
	e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 105.0, Volume: 2000, TS: ts}
	waitFor(t, done, 2*time.Second)

	// Tick 3: price DOWN to 102, volume 3000 → delta 1000 classified as SELL
	ts = ts.Add(time.Second)
	e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 102.0, Volume: 3000, TS: ts}
	waitFor(t, done, 2*time.Second)

	s := e.Stock("INE001")
	// Tick 1: LastDirection starts at 0 (>=0 → buy). buyVol += 1000
	// Tick 2: price > lastLTP → direction=+1, buyVol += 1000. Total buy = 2000
	// Tick 3: price < lastLTP → direction=-1, sellVol += 1000. Total sell = 1000
	if s.CumulativeBuyVol != 2000 {
		t.Errorf("CumulativeBuyVol = %d, want 2000", s.CumulativeBuyVol)
	}
	if s.CumulativeSellVol != 1000 {
		t.Errorf("CumulativeSellVol = %d, want 1000", s.CumulativeSellVol)
	}
	if s.LastDirection != -1 {
		t.Errorf("LastDirection = %d, want -1", s.LastDirection)
	}
}

func TestFeatureEngine_MultiStockDeltaTracking(t *testing.T) {
	e := NewFeatureEngine(DefaultEngineConfig())
	e.RegisterStock("INE001", "RELIANCE", "NIFTY_50")
	e.RegisterStock("INE002", "TCS", "NIFTY_IT")
	e.RegisterSector("NIFTY_50", []string{"INE001"})
	e.RegisterSector("NIFTY_IT", []string{"INE002"})

	// Set PrevClose so up/down tracking works
	e.Stock("INE001").PrevClose = 2400.0
	e.Stock("INE002").PrevClose = 3500.0

	e.Session().SessionStart(time.Now())

	tickCount := 0
	allDone := make(chan struct{}, 16)
	e.SetOnTick(func(isin string) {
		tickCount++
		if tickCount >= 2 {
			allDone <- struct{}{}
		}
	})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go e.Run(ctx)

	ts := time.Now()
	// RELIANCE: price above prev close → up
	e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 2500.0, Volume: 5000, TS: ts}
	// TCS: price below prev close → down
	e.TickChan() <- TickEvent{ISIN: "INE002", LTP: 3400.0, Volume: 3000, TS: ts.Add(time.Millisecond)}

	select {
	case <-allDone:
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for both ticks")
	}

	m := e.Market()
	if m.TotalStocks != 2 {
		t.Errorf("TotalStocks = %d, want 2", m.TotalStocks)
	}
	if m.StocksUp != 1 {
		t.Errorf("StocksUp = %d, want 1", m.StocksUp)
	}
	if m.StocksDown != 1 {
		t.Errorf("StocksDown = %d, want 1", m.StocksDown)
	}
	if m.TotalMarketVolume != 8000 {
		t.Errorf("TotalMarketVolume = %d, want 8000", m.TotalMarketVolume)
	}

	// Verify sector aggregation
	nifty50 := e.sectors["NIFTY_50"]
	if nifty50.TotalVolume != 5000 {
		t.Errorf("NIFTY_50 TotalVolume = %d, want 5000", nifty50.TotalVolume)
	}
	niftyIT := e.sectors["NIFTY_IT"]
	if niftyIT.TotalVolume != 3000 {
		t.Errorf("NIFTY_IT TotalVolume = %d, want 3000", niftyIT.TotalVolume)
	}
}
