package features

import (
	"testing"
	"time"
)

func TestStockState_Defaults(t *testing.T) {
	var s StockState

	if s.LTP != 0 {
		t.Errorf("LTP: got %f, want 0", s.LTP)
	}
	if s.LastDirection != 0 {
		t.Errorf("LastDirection: got %d, want 0", s.LastDirection)
	}
	if s.CumulativeVolume != 0 {
		t.Errorf("CumulativeVolume: got %d, want 0", s.CumulativeVolume)
	}
	if s.HasDepth {
		t.Error("HasDepth: got true, want false")
	}
	if s.prevWasUp || s.prevWasDown || s.prevWasAboveVWAP {
		t.Error("delta tracking bools should be false at zero value")
	}
	if s.prevBuyVol != 0 || s.prevSellVol != 0 || s.prevVolume != 0 || s.prevTurnover != 0 {
		t.Error("delta tracking counters should be zero at zero value")
	}
	if s.VolumeSlot != nil {
		t.Error("VolumeSlot should be nil at zero value (lazy init)")
	}
}

func TestMarketState_DeltaTracking(t *testing.T) {
	m := &MarketState{}
	s := &StockState{
		ISIN:      "INE002A01018",
		PrevClose: 100.0,
	}

	// First update: stock is up, above VWAP
	s.LTP = 105.0
	s.CumulativeVolume = 1000
	s.CumulativeTurnover = 105000
	s.CumulativeBuyVol = 600
	s.CumulativeSellVol = 400
	m.UpdateFromStock(s, 102.0) // vwap=102, LTP=105 → above VWAP

	if m.StocksUp != 1 {
		t.Errorf("after first update: StocksUp = %d, want 1", m.StocksUp)
	}
	if m.StocksDown != 0 {
		t.Errorf("after first update: StocksDown = %d, want 0", m.StocksDown)
	}
	if m.StocksFlat != 0 {
		t.Errorf("after first update: StocksFlat = %d, want 0", m.StocksFlat)
	}
	if m.StocksAboveVWAP != 1 {
		t.Errorf("after first update: StocksAboveVWAP = %d, want 1", m.StocksAboveVWAP)
	}
	if m.TotalMarketVolume != 1000 {
		t.Errorf("after first update: TotalMarketVolume = %d, want 1000", m.TotalMarketVolume)
	}
	if m.TotalMarketBuyVol != 600 {
		t.Errorf("after first update: TotalMarketBuyVol = %d, want 600", m.TotalMarketBuyVol)
	}

	// Second update: stock goes down, below VWAP — verify no double-counting
	s.LTP = 95.0
	s.CumulativeVolume = 2000
	s.CumulativeTurnover = 200000
	s.CumulativeBuyVol = 800
	s.CumulativeSellVol = 1200
	m.UpdateFromStock(s, 102.0) // vwap=102, LTP=95 → below VWAP

	if m.StocksUp != 0 {
		t.Errorf("after second update: StocksUp = %d, want 0 (delta should remove old)", m.StocksUp)
	}
	if m.StocksDown != 1 {
		t.Errorf("after second update: StocksDown = %d, want 1", m.StocksDown)
	}
	if m.StocksFlat != 0 {
		t.Errorf("after second update: StocksFlat = %d, want 0", m.StocksFlat)
	}
	if m.StocksAboveVWAP != 0 {
		t.Errorf("after second update: StocksAboveVWAP = %d, want 0 (delta should remove old)", m.StocksAboveVWAP)
	}
	if m.TotalMarketVolume != 2000 {
		t.Errorf("after second update: TotalMarketVolume = %d, want 2000", m.TotalMarketVolume)
	}
	if m.TotalMarketBuyVol != 800 {
		t.Errorf("after second update: TotalMarketBuyVol = %d, want 800", m.TotalMarketBuyVol)
	}
	if m.TotalMarketSellVol != 1200 {
		t.Errorf("after second update: TotalMarketSellVol = %d, want 1200", m.TotalMarketSellVol)
	}
	if m.TotalMarketTurnover != 200000 {
		t.Errorf("after second update: TotalMarketTurnover = %f, want 200000", m.TotalMarketTurnover)
	}
}

func TestSectorState_DeltaTracking(t *testing.T) {
	sec := &SectorState{
		Name:        "NIFTY_BANK",
		MemberISINs: []string{"INE002A01018"},
	}
	s := &StockState{
		ISIN:      "INE002A01018",
		SectorID:  "NIFTY_BANK",
		PrevClose: 500.0,
	}

	// First update: stock is up
	s.LTP = 520.0
	s.CumulativeVolume = 5000
	s.CumulativeTurnover = 2600000
	s.CumulativeBuyVol = 3000
	s.CumulativeSellVol = 2000
	sec.UpdateFromStock(s, 510.0) // above VWAP

	if sec.StocksUp != 1 {
		t.Errorf("after first update: StocksUp = %d, want 1", sec.StocksUp)
	}
	if sec.StocksAboveVWAP != 1 {
		t.Errorf("after first update: StocksAboveVWAP = %d, want 1", sec.StocksAboveVWAP)
	}
	if sec.TotalVolume != 5000 {
		t.Errorf("after first update: TotalVolume = %d, want 5000", sec.TotalVolume)
	}

	// Second update: stock now down, below VWAP — no double-counting
	s.LTP = 480.0
	s.CumulativeVolume = 10000
	s.CumulativeTurnover = 4900000
	s.CumulativeBuyVol = 4000
	s.CumulativeSellVol = 6000
	sec.UpdateFromStock(s, 510.0) // below VWAP

	if sec.StocksUp != 0 {
		t.Errorf("after second update: StocksUp = %d, want 0", sec.StocksUp)
	}
	if sec.StocksDown != 1 {
		t.Errorf("after second update: StocksDown = %d, want 1", sec.StocksDown)
	}
	if sec.StocksAboveVWAP != 0 {
		t.Errorf("after second update: StocksAboveVWAP = %d, want 0", sec.StocksAboveVWAP)
	}
	if sec.TotalVolume != 10000 {
		t.Errorf("after second update: TotalVolume = %d, want 10000", sec.TotalVolume)
	}
	if sec.TotalBuyVol != 4000 {
		t.Errorf("after second update: TotalBuyVol = %d, want 4000", sec.TotalBuyVol)
	}
	if sec.TotalSellVol != 6000 {
		t.Errorf("after second update: TotalSellVol = %d, want 6000", sec.TotalSellVol)
	}
	if sec.TotalTurnover != 4900000 {
		t.Errorf("after second update: TotalTurnover = %f, want 4900000", sec.TotalTurnover)
	}
}

func TestMarketState_MultipleStocks(t *testing.T) {
	m := &MarketState{}
	s1 := &StockState{ISIN: "INE001", PrevClose: 100.0}
	s2 := &StockState{ISIN: "INE002", PrevClose: 200.0}
	s3 := &StockState{ISIN: "INE003", PrevClose: 300.0}

	// s1 up, s2 down, s3 flat (LTP == PrevClose)
	s1.LTP = 110.0
	s1.CumulativeVolume = 100
	s1.CumulativeBuyVol = 60
	s1.CumulativeSellVol = 40
	m.UpdateFromStock(s1, 105.0)

	s2.LTP = 190.0
	s2.CumulativeVolume = 200
	s2.CumulativeBuyVol = 80
	s2.CumulativeSellVol = 120
	m.UpdateFromStock(s2, 195.0)

	s3.LTP = 300.0 // exactly equal to PrevClose → flat
	s3.CumulativeVolume = 50
	s3.CumulativeBuyVol = 25
	s3.CumulativeSellVol = 25
	m.UpdateFromStock(s3, 300.0)

	if m.StocksUp != 1 {
		t.Errorf("StocksUp = %d, want 1", m.StocksUp)
	}
	if m.StocksDown != 1 {
		t.Errorf("StocksDown = %d, want 1", m.StocksDown)
	}
	if m.StocksFlat != 1 {
		t.Errorf("StocksFlat = %d, want 1", m.StocksFlat)
	}
	if m.TotalMarketVolume != 350 {
		t.Errorf("TotalMarketVolume = %d, want 350", m.TotalMarketVolume)
	}

	// Now s1 goes flat, s2 goes up, s3 stays flat
	s1.LTP = 100.0
	s1.CumulativeVolume = 200
	s1.CumulativeBuyVol = 100
	s1.CumulativeSellVol = 100
	m.UpdateFromStock(s1, 100.0) // LTP == PrevClose → flat, LTP == vwap → not above

	s2.LTP = 210.0
	s2.CumulativeVolume = 400
	s2.CumulativeBuyVol = 250
	s2.CumulativeSellVol = 150
	m.UpdateFromStock(s2, 200.0)

	if m.StocksUp != 1 {
		t.Errorf("after flip: StocksUp = %d, want 1 (only s2)", m.StocksUp)
	}
	if m.StocksDown != 0 {
		t.Errorf("after flip: StocksDown = %d, want 0", m.StocksDown)
	}
	if m.StocksFlat != 2 {
		t.Errorf("after flip: StocksFlat = %d, want 2 (s1 + s3)", m.StocksFlat)
	}
	if m.TotalMarketVolume != 650 {
		t.Errorf("after flip: TotalMarketVolume = %d, want 650 (200+400+50)", m.TotalMarketVolume)
	}
}

func TestSlotVolumeAccumulator(t *testing.T) {
	e := NewFeatureEngine(nil)
	e.RegisterStock("INE001", "TEST", "")
	e.session.SessionStart(time.Date(2026, 3, 24, 9, 0, 0, 0, time.Local))

	slot0Time := time.Date(2026, 3, 24, 9, 16, 0, 0, time.Local) // slot 0
	slot1Time := time.Date(2026, 3, 24, 9, 21, 0, 0, time.Local) // slot 1

	// Test 1: First tick sets slot and volume
	e.handleTick(TickEvent{ISIN: "INE001", LTP: 100, Volume: 500, TS: slot0Time})
	s := e.Stock("INE001")
	if !s.CurrentSlotSet {
		t.Fatal("CurrentSlotSet should be true after first tick")
	}
	if s.CurrentSlot != 0 {
		t.Errorf("CurrentSlot = %d, want 0", s.CurrentSlot)
	}
	if s.CurrentSlotVol != 500 {
		t.Errorf("CurrentSlotVol = %d, want 500", s.CurrentSlotVol)
	}

	// Test 2: Same slot accumulates
	e.handleTick(TickEvent{ISIN: "INE001", LTP: 101, Volume: 800, TS: slot0Time.Add(30 * time.Second)})
	if s.CurrentSlotVol != 800 {
		t.Errorf("CurrentSlotVol = %d, want 800 (500 + 300)", s.CurrentSlotVol)
	}

	// Test 3: New slot resets accumulator
	e.handleTick(TickEvent{ISIN: "INE001", LTP: 102, Volume: 1000, TS: slot1Time})
	if s.CurrentSlot != 1 {
		t.Errorf("CurrentSlot = %d, want 1", s.CurrentSlot)
	}
	if s.CurrentSlotVol != 200 {
		t.Errorf("CurrentSlotVol = %d, want 200 (delta from 800 to 1000)", s.CurrentSlotVol)
	}
}
