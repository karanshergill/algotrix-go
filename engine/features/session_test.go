package features

import (
	"testing"
	"time"
)

func makeTestSession() (*SessionManager, map[string]*StockState, *MarketState, map[string]*SectorState) {
	stocks := map[string]*StockState{
		"INE001": {ISIN: "INE001", Symbol: "RELIANCE", SectorID: "ENERGY"},
		"INE002": {ISIN: "INE002", Symbol: "TCS", SectorID: "IT"},
	}
	market := &MarketState{}
	sectors := map[string]*SectorState{
		"ENERGY": {Name: "ENERGY", MemberISINs: []string{"INE001"}},
		"IT":     {Name: "IT", MemberISINs: []string{"INE002"}},
	}
	sm := NewSessionManager(stocks, market, sectors)
	return sm, stocks, market, sectors
}

func TestSessionManager_Lifecycle(t *testing.T) {
	sm, _, _, _ := makeTestSession()

	if sm.State() != SessionPreOpen {
		t.Fatalf("expected SessionPreOpen, got %d", sm.State())
	}

	sm.SessionStart(time.Date(2026, 3, 23, 9, 15, 0, 0, time.UTC))
	if sm.State() != SessionOpen {
		t.Fatalf("expected SessionOpen after start, got %d", sm.State())
	}

	sm.SessionEnd()
	if sm.State() != SessionClosed {
		t.Fatalf("expected SessionClosed after end, got %d", sm.State())
	}
}

func TestSessionManager_ResetPreservesBaselines(t *testing.T) {
	sm, stocks, _, _ := makeTestSession()

	s := stocks["INE001"]
	s.PrevClose = 2500.0
	s.ATR14d = 45.5
	s.AvgDailyVolume = 1_000_000
	s.VolumeSlot = map[int]VolumeSlotBaseline{
		1: {Mean: 500, StdDev: 50, Samples: 20},
	}
	s.SectorID = "ENERGY"

	sm.SessionStart(time.Date(2026, 3, 23, 9, 15, 0, 0, time.UTC))

	if s.PrevClose != 2500.0 {
		t.Errorf("PrevClose not preserved: got %f", s.PrevClose)
	}
	if s.ATR14d != 45.5 {
		t.Errorf("ATR14d not preserved: got %f", s.ATR14d)
	}
	if s.AvgDailyVolume != 1_000_000 {
		t.Errorf("AvgDailyVolume not preserved: got %d", s.AvgDailyVolume)
	}
	if len(s.VolumeSlot) != 1 || s.VolumeSlot[1].Mean != 500 {
		t.Errorf("VolumeSlot not preserved")
	}
	if s.SectorID != "ENERGY" {
		t.Errorf("SectorID not preserved: got %s", s.SectorID)
	}
	if s.ISIN != "INE001" {
		t.Errorf("ISIN not preserved: got %s", s.ISIN)
	}
	if s.Symbol != "RELIANCE" {
		t.Errorf("Symbol not preserved: got %s", s.Symbol)
	}
}

func TestSessionManager_ResetClearsIntraday(t *testing.T) {
	sm, stocks, market, sectors := makeTestSession()

	s := stocks["INE001"]
	s.LTP = 2550.0
	s.DayOpen = 2510.0
	s.DayHigh = 2560.0
	s.DayLow = 2490.0
	s.CumulativeVolume = 500_000
	s.CumulativeTurnover = 1_200_000_000
	s.CumulativeBuyVol = 300_000
	s.CumulativeSellVol = 200_000
	s.UpdateCount = 42
	s.LastDirection = 1
	s.HasDepth = true
	s.TotalBidQty = 10000
	s.TotalAskQty = 8000

	// Set rolling windows
	s.Volume1m = NewRollingSum(60*time.Second, 1000)
	s.Volume5m = NewRollingSum(300*time.Second, 5000)

	// Set market/sector state to non-zero
	market.StocksUp = 5
	market.TotalMarketVolume = 999
	sectors["ENERGY"].StocksUp = 2

	sm.SessionStart(time.Date(2026, 3, 23, 9, 15, 0, 0, time.UTC))

	if s.LTP != 0 {
		t.Errorf("LTP not cleared: got %f", s.LTP)
	}
	if s.DayOpen != 0 {
		t.Errorf("DayOpen not cleared: got %f", s.DayOpen)
	}
	if s.DayHigh != 0 {
		t.Errorf("DayHigh not cleared: got %f", s.DayHigh)
	}
	if s.DayLow != 0 {
		t.Errorf("DayLow not cleared: got %f", s.DayLow)
	}
	if s.CumulativeVolume != 0 {
		t.Errorf("CumulativeVolume not cleared: got %d", s.CumulativeVolume)
	}
	if s.CumulativeTurnover != 0 {
		t.Errorf("CumulativeTurnover not cleared: got %f", s.CumulativeTurnover)
	}
	if s.UpdateCount != 0 {
		t.Errorf("UpdateCount not cleared: got %d", s.UpdateCount)
	}
	if s.LastDirection != 0 {
		t.Errorf("LastDirection not cleared: got %d", s.LastDirection)
	}
	if s.HasDepth {
		t.Errorf("HasDepth not cleared")
	}
	if s.TotalBidQty != 0 {
		t.Errorf("TotalBidQty not cleared: got %d", s.TotalBidQty)
	}

	// Market state should be reset
	if market.StocksUp != 0 {
		t.Errorf("MarketState.StocksUp not cleared: got %d", market.StocksUp)
	}
	if market.TotalStocks != 2 {
		t.Errorf("MarketState.TotalStocks wrong: got %d, want 2", market.TotalStocks)
	}

	// Sector state should be reset but preserve Name/MemberISINs/TotalStocks
	sec := sectors["ENERGY"]
	if sec.StocksUp != 0 {
		t.Errorf("SectorState.StocksUp not cleared: got %d", sec.StocksUp)
	}
	if sec.Name != "ENERGY" {
		t.Errorf("SectorState.Name not preserved: got %s", sec.Name)
	}
	if sec.TotalStocks != 1 {
		t.Errorf("SectorState.TotalStocks wrong: got %d, want 1", sec.TotalStocks)
	}
}

func TestSessionManager_ResetClearsDeltaTracking(t *testing.T) {
	sm, stocks, _, _ := makeTestSession()

	s := stocks["INE001"]
	s.prevRegistered = true
	s.prevWasUp = true
	s.prevWasDown = false
	s.prevWasAboveVWAP = true
	s.prevBuyVol = 100_000
	s.prevSellVol = 50_000
	s.prevVolume = 150_000
	s.prevTurnover = 500_000_000

	sm.SessionStart(time.Date(2026, 3, 23, 9, 15, 0, 0, time.UTC))

	if s.prevRegistered {
		t.Errorf("prevRegistered not cleared")
	}
	if s.prevWasUp {
		t.Errorf("prevWasUp not cleared")
	}
	if s.prevWasAboveVWAP {
		t.Errorf("prevWasAboveVWAP not cleared")
	}
	if s.prevBuyVol != 0 {
		t.Errorf("prevBuyVol not cleared: got %d", s.prevBuyVol)
	}
	if s.prevSellVol != 0 {
		t.Errorf("prevSellVol not cleared: got %d", s.prevSellVol)
	}
	if s.prevVolume != 0 {
		t.Errorf("prevVolume not cleared: got %d", s.prevVolume)
	}
	if s.prevTurnover != 0 {
		t.Errorf("prevTurnover not cleared: got %f", s.prevTurnover)
	}
}

func TestSessionManager_IsAccepting(t *testing.T) {
	sm, _, _, _ := makeTestSession()

	// PreOpen — not accepting
	if sm.IsAccepting() {
		t.Errorf("IsAccepting should be false in PreOpen")
	}

	// Open — accepting
	sm.SessionStart(time.Date(2026, 3, 23, 9, 15, 0, 0, time.UTC))
	if !sm.IsAccepting() {
		t.Errorf("IsAccepting should be true when SessionOpen")
	}

	// Closed — not accepting
	sm.SessionEnd()
	if sm.IsAccepting() {
		t.Errorf("IsAccepting should be false when SessionClosed")
	}
}
