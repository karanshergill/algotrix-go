package features

import (
	"context"
	"math"
	"sync"
	"testing"
	"time"
)

func TestAllFeatures_Computation(t *testing.T) {
	e := NewFeatureEngine(DefaultEngineConfig())
	e.SetSyncSnapshot(true)

	// Register stock with baselines
	e.RegisterStock("INE001", "RELIANCE", "NIFTY_BANK")
	e.RegisterStock("INE002", "TCS", "NIFTY_BANK")
	e.RegisterSector("NIFTY_BANK", []string{"INE001", "INE002"})

	s1 := e.Stock("INE001")
	s1.PrevClose = 2400.0
	s1.ATR14d = 50.0
	s1.VolumeSlot = map[int]VolumeSlotBaseline{
		0: {Mean: 500000, StdDev: 100000, Samples: 20},
	}

	s2 := e.Stock("INE002")
	s2.PrevClose = 3500.0

	e.Session().SessionStart(time.Now())

	tickDone := make(chan string, 32)
	depthDone := make(chan string, 32)
	e.SetOnTick(func(isin string) { tickDone <- isin })
	e.SetOnDepth(func(isin string) { depthDone <- isin })

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go e.Run(ctx)

	// Use a time during market hours for slot matching
	baseTS := time.Date(2026, 3, 23, 9, 16, 0, 0, time.Local)

	// Send ticks for INE001 — 3 ticks to build state
	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2450.0, Volume: 100000, TS: baseTS}
	waitFor(t, tickDone, 2*time.Second)

	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2480.0, Volume: 200000, TS: baseTS.Add(time.Second)}
	waitFor(t, tickDone, 2*time.Second)

	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2460.0, Volume: 350000, TS: baseTS.Add(2 * time.Second)}
	waitFor(t, tickDone, 2*time.Second)

	// Send tick for INE002 (so market/sector breadth has data)
	e.TickChan() <- TickEvent{ISIN: "INE002", Symbol: "TCS", LTP: 3550.0, Volume: 50000, TS: baseTS.Add(3 * time.Second)}
	waitFor(t, tickDone, 2*time.Second)

	// Send depth for INE001
	e.DepthChan() <- DepthEvent{
		ISIN: "INE001",
		Bids: []DepthLevel{
			{Price: 2459.0, Qty: 500},
			{Price: 2458.0, Qty: 300},
			{Price: 2457.0, Qty: 200},
			{Price: 2456.0, Qty: 100},
			{Price: 2455.0, Qty: 50},
		},
		Asks: []DepthLevel{
			{Price: 2461.0, Qty: 400},
			{Price: 2462.0, Qty: 350},
			{Price: 2463.0, Qty: 250},
			{Price: 2464.0, Qty: 150},
			{Price: 2465.0, Qty: 100},
		},
		TS: baseTS.Add(4 * time.Second),
	}
	select {
	case <-depthDone:
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for depth callback")
	}

	// Now verify snapshot has features
	snap := e.Snapshot()
	stock := snap.Stocks["INE001"]

	// Check tick-triggered features from latest tick snapshot
	// We need to send another tick to get fresh features in snapshot
	// (the depth event overwrites snapshot with depth features only)
	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2465.0, Volume: 400000, TS: baseTS.Add(5 * time.Second)}
	waitFor(t, tickDone, 2*time.Second)

	snap = e.Snapshot()
	stock = snap.Stocks["INE001"]

	// === Price features ===
	assertFeatureNonZero(t, stock.Features, "vwap")
	assertFeatureExists(t, stock.Features, "vwap_dist_bps") // can be zero if LTP == VWAP
	assertFeatureNonZero(t, stock.Features, "change_pct")
	assertFeatureNonZero(t, stock.Features, "day_range_pct")
	assertFeatureNonZero(t, stock.Features, "exhaustion")

	// === Volume features ===
	assertFeatureExists(t, stock.Features, "volume_spike_z")
	assertFeatureNonZero(t, stock.Features, "buy_pressure")
	assertFeatureNonZero(t, stock.Features, "buy_pressure_5m")
	assertFeatureExists(t, stock.Features, "update_intensity")

	// === Breadth features ===
	assertFeatureNonZero(t, stock.Features, "breadth_ratio")
	assertFeatureExists(t, stock.Features, "vwap_breadth")
	assertFeatureNonZero(t, stock.Features, "market_buy_pressure")

	// === Sector features ===
	assertFeatureNonZero(t, stock.Features, "sector_breadth")
	assertFeatureNonZero(t, stock.Features, "sector_buy_pressure")

	// === Book features (need depth snapshot) ===
	// Get depth snapshot — send another depth to get fresh features
	e.DepthChan() <- DepthEvent{
		ISIN: "INE001",
		Bids: []DepthLevel{
			{Price: 2464.0, Qty: 600},
			{Price: 2463.0, Qty: 400},
			{Price: 2462.0, Qty: 300},
			{Price: 2461.0, Qty: 200},
			{Price: 2460.0, Qty: 100},
		},
		Asks: []DepthLevel{
			{Price: 2466.0, Qty: 500},
			{Price: 2467.0, Qty: 350},
			{Price: 2468.0, Qty: 250},
			{Price: 2469.0, Qty: 150},
			{Price: 2470.0, Qty: 100},
		},
		TS: baseTS.Add(6 * time.Second),
	}
	select {
	case <-depthDone:
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for depth callback")
	}

	snap = e.Snapshot()
	depthStock := snap.Stocks["INE001"]

	assertFeatureNonZero(t, depthStock.Features, "book_imbalance")
	assertFeatureNonZero(t, depthStock.Features, "book_imbalance_weighted")
	assertFeatureNonZero(t, depthStock.Features, "spread_bps")

	// Verify reasonable values
	vwap := stock.Features["vwap"]
	if vwap < 2400 || vwap > 2500 {
		t.Errorf("vwap = %f, expected between 2400-2500", vwap)
	}

	changePct := stock.Features["change_pct"]
	if changePct < 0 || changePct > 5 {
		t.Errorf("change_pct = %f, expected positive and < 5%%", changePct)
	}

	bp := stock.Features["buy_pressure"]
	if bp < 0 || bp > 1 {
		t.Errorf("buy_pressure = %f, expected 0-1", bp)
	}

	bi := depthStock.Features["book_imbalance"]
	if bi < 0 || bi > 1 {
		t.Errorf("book_imbalance = %f, expected 0-1", bi)
	}

	t.Logf("All 17 features computed successfully")
	t.Logf("Tick features: %v", stock.Features)
	t.Logf("Depth features: %v", depthStock.Features)
}

func assertFeatureNonZero(t *testing.T, features map[string]float64, name string) {
	t.Helper()
	v, ok := features[name]
	if !ok {
		t.Errorf("feature %q missing from map", name)
		return
	}
	if v == 0 || math.IsNaN(v) {
		t.Errorf("feature %q = %f, expected non-zero", name, v)
	}
}

func assertFeatureExists(t *testing.T, features map[string]float64, name string) {
	t.Helper()
	if _, ok := features[name]; !ok {
		t.Errorf("feature %q missing from map", name)
	}
}

func assertFeatureApprox(t *testing.T, features map[string]float64, name string, want, tolerance float64) {
	t.Helper()
	v, ok := features[name]
	if !ok {
		t.Errorf("feature %q missing from map", name)
		return
	}
	if math.Abs(v-want) > tolerance {
		t.Errorf("feature %q = %f, want %f (±%f)", name, v, want, tolerance)
	}
}

func waitForDepth(t *testing.T, ch <-chan string, timeout time.Duration) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(timeout):
		t.Fatal("timed out waiting for depth callback")
	}
}

// ---------------------------------------------------------------------------
// TestFullPipeline_EndToEnd — 3 stocks, 2 sectors, 10+ ticks each, full verify
// ---------------------------------------------------------------------------

func TestFullPipeline_EndToEnd(t *testing.T) {
	e := NewFeatureEngine(DefaultEngineConfig())
	e.SetSyncSnapshot(true)

	// Register 3 stocks across 2 sectors
	e.RegisterStock("INE001", "RELIANCE", "ENERGY")
	e.RegisterStock("INE002", "TCS", "IT")
	e.RegisterStock("INE003", "INFY", "IT")
	e.RegisterSector("ENERGY", []string{"INE001"})
	e.RegisterSector("IT", []string{"INE002", "INE003"})

	// Set baselines for all stocks
	for _, isin := range []string{"INE001", "INE002", "INE003"} {
		s := e.Stock(isin)
		s.ATR14d = 40.0
		s.VolumeSlot = map[int]VolumeSlotBaseline{
			0: {Mean: 100000, StdDev: 20000, Samples: 20},
		}
		s.AvgDailyVolume = 5_000_000
	}
	e.Stock("INE001").PrevClose = 2400.0
	e.Stock("INE002").PrevClose = 3500.0
	e.Stock("INE003").PrevClose = 1800.0

	e.Session().SessionStart(time.Now())

	tickDone := make(chan string, 128)
	depthDone := make(chan string, 128)
	e.SetOnTick(func(isin string) { tickDone <- isin })
	e.SetOnDepth(func(isin string) { depthDone <- isin })

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go e.Run(ctx)

	baseTS := time.Date(2026, 3, 23, 9, 16, 0, 0, time.Local)

	// Send 10+ ticks per stock with realistic price movement
	// INE001 (RELIANCE): trending up from 2400 → ~2450
	relPrices := []float64{2410, 2420, 2415, 2430, 2440, 2435, 2445, 2450, 2448, 2455, 2460}
	// INE002 (TCS): trending down from 3500 → ~3460
	tcsPrices := []float64{3490, 3480, 3485, 3475, 3470, 3472, 3465, 3460, 3462, 3458, 3455}
	// INE003 (INFY): mostly flat around 1800
	infyPrices := []float64{1802, 1800, 1798, 1801, 1803, 1799, 1800, 1802, 1801, 1800, 1799}

	tickIdx := 0
	for i := 0; i < 11; i++ {
		ts := baseTS.Add(time.Duration(i) * time.Second)
		vol := int64((i + 1) * 10000)

		e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: relPrices[i], Volume: vol, TS: ts}
		tickIdx++
		e.TickChan() <- TickEvent{ISIN: "INE002", Symbol: "TCS", LTP: tcsPrices[i], Volume: vol, TS: ts.Add(time.Millisecond)}
		tickIdx++
		e.TickChan() <- TickEvent{ISIN: "INE003", Symbol: "INFY", LTP: infyPrices[i], Volume: vol, TS: ts.Add(2 * time.Millisecond)}
		tickIdx++
	}

	// Drain all tick callbacks
	for i := 0; i < tickIdx; i++ {
		waitFor(t, tickDone, 3*time.Second)
	}

	// Send depth for each stock
	for _, isin := range []string{"INE001", "INE002", "INE003"} {
		e.DepthChan() <- DepthEvent{
			ISIN: isin,
			Bids: []DepthLevel{
				{Price: 100.0, Qty: 500},
				{Price: 99.0, Qty: 400},
				{Price: 98.0, Qty: 300},
				{Price: 97.0, Qty: 200},
				{Price: 96.0, Qty: 100},
			},
			Asks: []DepthLevel{
				{Price: 101.0, Qty: 450},
				{Price: 102.0, Qty: 350},
				{Price: 103.0, Qty: 250},
				{Price: 104.0, Qty: 150},
				{Price: 105.0, Qty: 50},
			},
			TS: baseTS.Add(12 * time.Second),
		}
		waitForDepth(t, depthDone, 3*time.Second)
	}

	// Send one more tick per stock to get tick features in snapshot
	for _, isin := range []string{"INE001", "INE002", "INE003"} {
		e.TickChan() <- TickEvent{ISIN: isin, LTP: e.Stock(isin).LTP, Volume: e.Stock(isin).CumulativeVolume + 1000, TS: baseTS.Add(13 * time.Second)}
		waitFor(t, tickDone, 3*time.Second)
	}

	snap := e.Snapshot()

	// (a) All 17 features present and reasonable for each stock with tick snapshot
	allTickFeatures := []string{
		"vwap", "vwap_dist_bps", "change_pct", "day_range_pct", "exhaustion",
		"volume_spike_z", "buy_pressure", "buy_pressure_5m", "update_intensity",
		"breadth_ratio", "vwap_breadth", "market_buy_pressure",
		"sector_breadth", "sector_buy_pressure",
	}
	for _, isin := range []string{"INE001", "INE002", "INE003"} {
		stock := snap.Stocks[isin]
		for _, fname := range allTickFeatures {
			v, ok := stock.Features[fname]
			if !ok {
				t.Errorf("[%s] feature %q missing", isin, fname)
				continue
			}
			if math.IsNaN(v) {
				t.Errorf("[%s] feature %q is NaN", isin, fname)
			}
		}
	}

	// (b) MarketSnapshot breadth: RELIANCE up, TCS down, INFY ~flat
	mkt := snap.Market
	if mkt.TotalStocks != 3 {
		t.Errorf("Market.TotalStocks = %d, want 3", mkt.TotalStocks)
	}
	if mkt.StocksUp < 1 {
		t.Errorf("Market.StocksUp = %d, expected >= 1 (RELIANCE up)", mkt.StocksUp)
	}
	if mkt.StocksDown < 1 {
		t.Errorf("Market.StocksDown = %d, expected >= 1 (TCS down)", mkt.StocksDown)
	}

	// (c) SectorSnapshots
	itSector := snap.Sectors["IT"]
	if itSector.TotalStocks != 2 {
		t.Errorf("IT sector TotalStocks = %d, want 2", itSector.TotalStocks)
	}
	if itSector.StocksDown < 1 {
		t.Errorf("IT sector StocksDown = %d, expected >= 1 (TCS down)", itSector.StocksDown)
	}
	energySector := snap.Sectors["ENERGY"]
	if energySector.TotalStocks != 1 {
		t.Errorf("ENERGY sector TotalStocks = %d, want 1", energySector.TotalStocks)
	}
	if energySector.StocksUp != 1 {
		t.Errorf("ENERGY sector StocksUp = %d, want 1", energySector.StocksUp)
	}

	// (d) QualityFlags: BaselineMissing=false (we set all baselines)
	for _, isin := range []string{"INE001", "INE002", "INE003"} {
		q := snap.Stocks[isin].Quality
		if q.BaselineMissing {
			t.Errorf("[%s] BaselineMissing should be false", isin)
		}
	}

	// Stop Run before session transition to avoid races
	cancel()
	// End session, verify SessionClosed
	e.Session().SessionEnd()
	if e.Session().State() != SessionClosed {
		t.Errorf("expected SessionClosed, got %d", e.Session().State())
	}
}

// ---------------------------------------------------------------------------
// TestFeatureValues_Correctness — exact value assertions for single tick
// ---------------------------------------------------------------------------

func TestFeatureValues_Correctness(t *testing.T) {
	e := NewFeatureEngine(DefaultEngineConfig())
	e.SetSyncSnapshot(true)
	e.RegisterStock("INE001", "TEST", "SEC1")
	e.RegisterSector("SEC1", []string{"INE001"})

	s := e.Stock("INE001")
	s.PrevClose = 100.0
	s.ATR14d = 10.0
	s.VolumeSlot = map[int]VolumeSlotBaseline{
		0: {Mean: 500, StdDev: 100, Samples: 20},
	}

	e.Session().SessionStart(time.Now())

	tickDone := make(chan string, 16)
	e.SetOnTick(func(isin string) { tickDone <- isin })

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go e.Run(ctx)

	// Send exactly 1 tick: LTP=105, Volume=1000
	ts := time.Date(2026, 3, 23, 9, 16, 0, 0, time.Local)
	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "TEST", LTP: 105.0, Volume: 1000, TS: ts}
	waitFor(t, tickDone, 2*time.Second)

	snap := e.Snapshot()
	feat := snap.Stocks["INE001"].Features

	// change_pct = (105 - 100) / 100 * 100 = 5.0
	assertFeatureApprox(t, feat, "change_pct", 5.0, 0.001)

	// VWAP = turnover / volume = 105*1000 / 1000 = 105.0
	assertFeatureApprox(t, feat, "vwap", 105.0, 0.001)

	// vwap_dist_bps = (LTP - VWAP) / VWAP * 10000 = (105 - 105) / 105 * 10000 = 0.0
	assertFeatureApprox(t, feat, "vwap_dist_bps", 0.0, 0.001)

	// buy_pressure: first tick, LastDirection starts at 0, >=0 → buy.
	// CumulativeBuyVol=1000, CumulativeSellVol=0, so buy_pressure = 1000/1000 = 1.0
	assertFeatureApprox(t, feat, "buy_pressure", 1.0, 0.001)

	// day_range_pct = (high - low) / prevClose * 100 = (105-105)/100*100 = 0.0
	assertFeatureApprox(t, feat, "day_range_pct", 0.0, 0.001)

	// exhaustion = abs(LTP - PrevClose) / ATR14d = abs(105-100)/10 = 0.5
	assertFeatureApprox(t, feat, "exhaustion", 0.5, 0.001)

	// update_intensity = Updates1m.Sum() = 1
	assertFeatureApprox(t, feat, "update_intensity", 1.0, 0.001)

	t.Logf("Feature values: %v", feat)
}

// ---------------------------------------------------------------------------
// TestConcurrentSnapshotRead — 10 goroutines × 100 reads, no races or panics
// ---------------------------------------------------------------------------

func TestConcurrentSnapshotRead(t *testing.T) {
	e := NewFeatureEngine(DefaultEngineConfig())
	e.SetSyncSnapshot(true)
	e.RegisterStock("INE001", "RELIANCE", "SEC1")
	e.RegisterSector("SEC1", []string{"INE001"})

	s := e.Stock("INE001")
	s.PrevClose = 2400.0
	s.ATR14d = 50.0
	s.VolumeSlot = map[int]VolumeSlotBaseline{
		0: {Mean: 100000, StdDev: 20000, Samples: 20},
	}

	e.Session().SessionStart(time.Now())

	tickDone := make(chan string, 1024)
	e.SetOnTick(func(isin string) { tickDone <- isin })

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go e.Run(ctx)

	// Start 10 goroutines reading Snapshot() concurrently
	var wg sync.WaitGroup
	const numReaders = 10
	const readsPerReader = 100

	for i := 0; i < numReaders; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < readsPerReader; j++ {
				snap := e.Snapshot()
				if snap == nil {
					t.Error("Snapshot() returned nil")
					return
				}
				// Access fields to ensure no data race on map reads
				_ = snap.Market.TotalStocks
				for _, ss := range snap.Stocks {
					_ = ss.LTP
					for _, v := range ss.Features {
						_ = v
					}
				}
				for _, sec := range snap.Sectors {
					_ = sec.StocksUp
				}
			}
		}()
	}

	// Concurrently send ticks to create snapshot updates
	baseTS := time.Date(2026, 3, 23, 9, 16, 0, 0, time.Local)
	const numTicks = 50
	for i := 0; i < numTicks; i++ {
		ts := baseTS.Add(time.Duration(i) * time.Second)
		ltp := 2400.0 + float64(i)
		vol := int64((i + 1) * 1000)
		e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: ltp, Volume: vol, TS: ts}
		waitFor(t, tickDone, 3*time.Second)
	}

	wg.Wait()

	// Final snapshot should be valid
	snap := e.Snapshot()
	if len(snap.Stocks) == 0 {
		t.Error("expected stocks in final snapshot")
	}
}

// ---------------------------------------------------------------------------
// TestSessionRestart — end session, start new, verify state cleared/preserved
// ---------------------------------------------------------------------------

func TestSessionRestart(t *testing.T) {
	e := NewFeatureEngine(DefaultEngineConfig())
	e.SetSyncSnapshot(true)
	e.RegisterStock("INE001", "RELIANCE", "SEC1")
	e.RegisterSector("SEC1", []string{"INE001"})

	s := e.Stock("INE001")
	s.PrevClose = 2400.0
	s.ATR14d = 50.0
	s.VolumeSlot = map[int]VolumeSlotBaseline{
		0: {Mean: 100000, StdDev: 20000, Samples: 20},
	}

	// Session 1
	e.Session().SessionStart(time.Now())

	tickDone := make(chan string, 64)
	e.SetOnTick(func(isin string) { tickDone <- isin })

	ctx1, cancel1 := context.WithCancel(context.Background())
	runDone := make(chan struct{})
	go func() {
		e.Run(ctx1)
		close(runDone)
	}()

	baseTS := time.Date(2026, 3, 23, 9, 16, 0, 0, time.Local)

	// Send ticks in session 1
	e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 2450.0, Volume: 10000, TS: baseTS}
	waitFor(t, tickDone, 2*time.Second)
	e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 2460.0, Volume: 20000, TS: baseTS.Add(time.Second)}
	waitFor(t, tickDone, 2*time.Second)

	// Verify state built
	if s.LTP != 2460.0 {
		t.Errorf("session1: LTP = %f, want 2460.0", s.LTP)
	}
	if s.CumulativeVolume != 20000 {
		t.Errorf("session1: CumulativeVolume = %d, want 20000", s.CumulativeVolume)
	}

	// Stop Run before session transitions to avoid races
	cancel1()
	<-runDone

	// End session 1
	e.Session().SessionEnd()
	if e.Session().State() != SessionClosed {
		t.Fatal("expected SessionClosed")
	}

	// Start session 2 — should clear intraday but preserve baselines
	e.Session().SessionStart(time.Now())

	// Verify intraday state is cleared
	if s.LTP != 0 {
		t.Errorf("session2: LTP not cleared: %f", s.LTP)
	}
	if s.CumulativeVolume != 0 {
		t.Errorf("session2: CumulativeVolume not cleared: %d", s.CumulativeVolume)
	}
	if s.DayOpen != 0 {
		t.Errorf("session2: DayOpen not cleared: %f", s.DayOpen)
	}
	if s.UpdateCount != 0 {
		t.Errorf("session2: UpdateCount not cleared: %d", s.UpdateCount)
	}

	// Verify baselines preserved
	if s.PrevClose != 2400.0 {
		t.Errorf("session2: PrevClose not preserved: %f", s.PrevClose)
	}
	if s.ATR14d != 50.0 {
		t.Errorf("session2: ATR14d not preserved: %f", s.ATR14d)
	}
	if len(s.VolumeSlot) != 1 {
		t.Errorf("session2: VolumeSlot not preserved")
	}

	// Restart Run for session 2
	ctx2, cancel2 := context.WithCancel(context.Background())
	defer cancel2()
	go e.Run(ctx2)

	// Send new ticks in session 2, verify features compute fresh
	ts2 := time.Date(2026, 3, 24, 9, 16, 0, 0, time.Local)
	e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 2500.0, Volume: 5000, TS: ts2}
	waitFor(t, tickDone, 2*time.Second)

	if s.LTP != 2500.0 {
		t.Errorf("session2 tick: LTP = %f, want 2500.0", s.LTP)
	}
	if s.DayOpen != 2500.0 {
		t.Errorf("session2 tick: DayOpen = %f, want 2500.0 (first tick of new session)", s.DayOpen)
	}
	if s.CumulativeVolume != 5000 {
		t.Errorf("session2 tick: CumulativeVolume = %d, want 5000", s.CumulativeVolume)
	}

	snap := e.Snapshot()
	feat := snap.Stocks["INE001"].Features
	// change_pct = (2500 - 2400) / 2400 * 100 ≈ 4.1667
	assertFeatureApprox(t, feat, "change_pct", 100.0/2400.0*100.0, 0.01)
}

// ---------------------------------------------------------------------------
// TestEdgeCases — boundary conditions that must not panic
// ---------------------------------------------------------------------------

func TestEdgeCases(t *testing.T) {
	t.Run("PrevClose_Zero", func(t *testing.T) {
		e := NewFeatureEngine(DefaultEngineConfig())
		e.SetSyncSnapshot(true)
		e.RegisterStock("INE001", "TEST", "SEC1")
		e.RegisterSector("SEC1", []string{"INE001"})
		// PrevClose defaults to 0 — do NOT set it

		e.Session().SessionStart(time.Now())

		tickDone := make(chan string, 16)
		e.SetOnTick(func(isin string) { tickDone <- isin })

		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		go e.Run(ctx)

		ts := time.Date(2026, 3, 23, 9, 16, 0, 0, time.Local)
		e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 100.0, Volume: 1000, TS: ts}
		waitFor(t, tickDone, 2*time.Second)

		snap := e.Snapshot()
		stock := snap.Stocks["INE001"]

		// change_pct should NOT be present (Ready returns false when PrevClose=0)
		if _, ok := stock.Features["change_pct"]; ok {
			t.Error("change_pct should not be ready when PrevClose=0")
		}
		// day_range_pct should NOT be present
		if _, ok := stock.Features["day_range_pct"]; ok {
			t.Error("day_range_pct should not be ready when PrevClose=0")
		}
		// vwap should still work
		assertFeatureExists(t, stock.Features, "vwap")

		// Quality: BaselineMissing=true (ATR14d=0 and VolumeSlot empty)
		if !stock.Quality.BaselineMissing {
			t.Error("BaselineMissing should be true when no baselines set")
		}
	})

	t.Run("NoDepth_BookFeaturesNotReady", func(t *testing.T) {
		e := NewFeatureEngine(DefaultEngineConfig())
		e.SetSyncSnapshot(true)
		e.RegisterStock("INE001", "TEST", "SEC1")
		e.RegisterSector("SEC1", []string{"INE001"})
		e.Stock("INE001").PrevClose = 100.0

		e.Session().SessionStart(time.Now())

		tickDone := make(chan string, 16)
		e.SetOnTick(func(isin string) { tickDone <- isin })

		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		go e.Run(ctx)

		ts := time.Date(2026, 3, 23, 9, 16, 0, 0, time.Local)
		e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 105.0, Volume: 1000, TS: ts}
		waitFor(t, tickDone, 2*time.Second)

		snap := e.Snapshot()
		stock := snap.Stocks["INE001"]

		// Book features are depth-triggered; with no depth event sent,
		// the snapshot should not contain them (they come from depth processing)
		// Tick-triggered features should be present
		assertFeatureExists(t, stock.Features, "vwap")
		assertFeatureExists(t, stock.Features, "change_pct")

		// Explicitly: HasDepth should be false
		if e.Stock("INE001").HasDepth {
			t.Error("HasDepth should be false when no depth sent")
		}
	})

	t.Run("NoVolumeSlot_VolumeSpikeNotReady", func(t *testing.T) {
		e := NewFeatureEngine(DefaultEngineConfig())
		e.SetSyncSnapshot(true)
		e.RegisterStock("INE001", "TEST", "SEC1")
		e.RegisterSector("SEC1", []string{"INE001"})
		s := e.Stock("INE001")
		s.PrevClose = 100.0
		s.ATR14d = 10.0
		// Do NOT set VolumeSlot

		e.Session().SessionStart(time.Now())

		tickDone := make(chan string, 16)
		e.SetOnTick(func(isin string) { tickDone <- isin })

		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		go e.Run(ctx)

		ts := time.Date(2026, 3, 23, 9, 16, 0, 0, time.Local)
		e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 105.0, Volume: 1000, TS: ts}
		waitFor(t, tickDone, 2*time.Second)

		snap := e.Snapshot()
		feat := snap.Stocks["INE001"].Features

		// volume_spike_z should NOT be present (no slot baseline)
		if _, ok := feat["volume_spike_z"]; ok {
			t.Error("volume_spike_z should not be ready when VolumeSlot is nil")
		}
		// Other features should still work
		assertFeatureExists(t, feat, "vwap")
		assertFeatureExists(t, feat, "buy_pressure")
	})

	t.Run("ZeroVolumeTick", func(t *testing.T) {
		e := NewFeatureEngine(DefaultEngineConfig())
		e.SetSyncSnapshot(true)
		e.RegisterStock("INE001", "TEST", "SEC1")
		e.RegisterSector("SEC1", []string{"INE001"})
		s := e.Stock("INE001")
		s.PrevClose = 100.0

		e.Session().SessionStart(time.Now())

		tickDone := make(chan string, 16)
		e.SetOnTick(func(isin string) { tickDone <- isin })

		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		go e.Run(ctx)

		ts := time.Date(2026, 3, 23, 9, 16, 0, 0, time.Local)

		// First tick with volume
		e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 105.0, Volume: 1000, TS: ts}
		waitFor(t, tickDone, 2*time.Second)

		// Second tick with SAME volume (zero delta) — price-only update
		e.TickChan() <- TickEvent{ISIN: "INE001", LTP: 107.0, Volume: 1000, TS: ts.Add(time.Second)}
		waitFor(t, tickDone, 2*time.Second)

		// LTP should update but volume should stay the same
		if s.LTP != 107.0 {
			t.Errorf("LTP = %f, want 107.0 after zero-volume tick", s.LTP)
		}
		if s.CumulativeVolume != 1000 {
			t.Errorf("CumulativeVolume = %d, want 1000 (unchanged after zero-volume tick)", s.CumulativeVolume)
		}
		// UpdateCount should be 2 (price-only ticks are still feed updates)
		if s.UpdateCount != 2 {
			t.Errorf("UpdateCount = %d, want 2 (price-only ticks still count as updates)", s.UpdateCount)
		}
		// DayHigh should reflect the new price
		if s.DayHigh != 107.0 {
			t.Errorf("DayHigh = %f, want 107.0", s.DayHigh)
		}
	})
}
