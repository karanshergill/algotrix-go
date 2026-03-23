package features

import "time"

// ---------------------------------------------------------------------------
// VolumeSlotBaseline holds historical volume stats for a 5-minute slot.
// Used to compute relative-volume-to-slot features.
// ---------------------------------------------------------------------------

// VolumeSlotBaseline stores mean and stddev of historical volume for a
// specific 5-minute intraday slot, enabling z-score comparisons.
type VolumeSlotBaseline struct {
	Mean    float64
	StdDev  float64
	Samples int
}

// ---------------------------------------------------------------------------
// StockState — per-ISIN mutable state, mutated only by the event loop.
//
// Delta Tracking Pattern:
// StockState carries "prev*" fields that record the values contributed to
// MarketState/SectorState on the LAST update. When a new tick arrives:
//   1. MarketState.UpdateFromStock subtracts the prev* contribution
//   2. Computes the new contribution from current fields
//   3. Adds the new contribution
//   4. Saves current values into prev* for the next cycle
// This avoids full re-scans of all stocks on every tick.
// ---------------------------------------------------------------------------

type StockState struct {
	ISIN     string
	Symbol   string
	SectorID string // e.g. "NIFTY_IT", "NIFTY_BANK", "NIFTY_FMCG"

	// === Tick-Updated Primitives ===
	LTP                float64
	DayOpen            float64
	DayHigh            float64
	DayLow             float64
	PrevClose          float64   // pre-loaded from atdb
	LastTickTS         time.Time

	CumulativeVolume   int64
	CumulativeTurnover float64
	CumulativeBuyVol   int64
	CumulativeSellVol  int64
	UpdateCount        int64 // feed updates, NOT trade count

	LastDirection int8    // +1 uptick, -1 downtick, 0 unchanged
	LastLTP       float64

	// === Depth-Updated Primitives ===
	BidPrices   [5]float64
	BidQtys     [5]int64
	AskPrices   [5]float64
	AskQtys     [5]int64
	TotalBidQty int64
	TotalAskQty int64
	HasDepth    bool      // true after first depth event (for readiness)
	LastDepthTS time.Time

	// === Rolling Windows ===
	Volume1m  *RollingSum     // 60s window
	Volume5m  *RollingSum     // 300s window
	BuyVol5m  *RollingSum     // 300s window
	SellVol5m *RollingSum     // 300s window
	Updates1m *RollingSum     // 60s window (activity proxy)
	High5m    *RollingExtreme // 300s max
	Low5m     *RollingExtreme // 300s min

	// === Pre-Loaded Baselines ===
	ATR14d         float64
	AvgDailyVolume int64
	VolumeSlot     map[int]VolumeSlotBaseline // mean + stddev per 5-min slot

	// === Delta Tracking (for MarketState/SectorState) ===
	// These fields record what was last contributed to aggregate state.
	// On every update cycle the aggregator subtracts prev*, computes new
	// values from current fields, adds them, then overwrites prev*.
	prevRegistered   bool // false until first UpdateFromStock; skip "remove old" on first call
	prevWasUp        bool
	prevWasDown      bool
	prevWasAboveVWAP bool
	prevBuyVol       int64
	prevSellVol      int64
	prevVolume       int64
	prevTurnover     float64
}

// ---------------------------------------------------------------------------
// SectorState — aggregate breadth/volume for one sector.
//
// Updated via delta tracking: each UpdateFromStock call removes the stock's
// old contribution (stored in StockState.prev* fields) and adds the new one.
// ---------------------------------------------------------------------------

type SectorState struct {
	Name        string   // e.g. "NIFTY_BANK"
	MemberISINs []string // populated at startup from sector mapping

	StocksUp        int
	StocksDown      int
	StocksAboveVWAP int
	TotalStocks     int
	TotalBuyVol     int64
	TotalSellVol    int64
	TotalVolume     int64
	TotalTurnover   float64
}

// UpdateFromStock performs a delta update for the given stock.
//
// Delta pattern:
//  1. Remove the stock's previous contribution (from prev* fields)
//  2. Compute the stock's current contribution from live fields
//  3. Add the new contribution
//  4. Save current values into prev* for the next cycle
//
// vwap must be passed in because VWAP is computed by the feature layer,
// not stored directly on StockState.
func (s *SectorState) UpdateFromStock(stock *StockState, vwap float64) {
	// --- Remove old contribution (skip on first call — nothing to remove) ---
	if stock.prevRegistered {
		if stock.prevWasUp {
			s.StocksUp--
		}
		if stock.prevWasDown {
			s.StocksDown--
		}
		if stock.prevWasAboveVWAP {
			s.StocksAboveVWAP--
		}
		s.TotalBuyVol -= stock.prevBuyVol
		s.TotalSellVol -= stock.prevSellVol
		s.TotalVolume -= stock.prevVolume
		s.TotalTurnover -= stock.prevTurnover
	}

	// --- Compute new contribution ---
	isUp := stock.LTP > stock.PrevClose && stock.PrevClose > 0
	isDown := stock.LTP < stock.PrevClose && stock.PrevClose > 0
	isAboveVWAP := vwap > 0 && stock.LTP > vwap

	// --- Add new contribution ---
	if isUp {
		s.StocksUp++
	}
	if isDown {
		s.StocksDown++
	}
	if isAboveVWAP {
		s.StocksAboveVWAP++
	}
	s.TotalBuyVol += stock.CumulativeBuyVol
	s.TotalSellVol += stock.CumulativeSellVol
	s.TotalVolume += stock.CumulativeVolume
	s.TotalTurnover += stock.CumulativeTurnover

	// --- Save for next delta ---
	stock.prevRegistered = true
	stock.prevWasUp = isUp
	stock.prevWasDown = isDown
	stock.prevWasAboveVWAP = isAboveVWAP
	stock.prevBuyVol = stock.CumulativeBuyVol
	stock.prevSellVol = stock.CumulativeSellVol
	stock.prevVolume = stock.CumulativeVolume
	stock.prevTurnover = stock.CumulativeTurnover
}

// ---------------------------------------------------------------------------
// MarketState — market-wide breadth and volume aggregates.
//
// Same delta tracking pattern as SectorState, but across ALL stocks.
// ---------------------------------------------------------------------------

type MarketState struct {
	TotalStocks     int
	StocksUp        int
	StocksDown      int
	StocksFlat      int
	StocksAboveVWAP int

	TotalMarketBuyVol   int64
	TotalMarketSellVol  int64
	TotalMarketVolume   int64
	TotalMarketTurnover float64

	NiftyLTP       float64
	NiftyPrevClose float64
	NiftyDayHigh   float64
	NiftyDayLow    float64
}

// UpdateFromStock performs a delta update for the given stock.
// See SectorState.UpdateFromStock for the pattern explanation.
func (m *MarketState) UpdateFromStock(s *StockState, vwap float64) {
	// --- Remove old contribution (skip on first call — nothing to remove) ---
	if s.prevRegistered {
		if s.prevWasUp {
			m.StocksUp--
		}
		if s.prevWasDown {
			m.StocksDown--
		}
		if !s.prevWasUp && !s.prevWasDown {
			m.StocksFlat--
		}
		if s.prevWasAboveVWAP {
			m.StocksAboveVWAP--
		}
		m.TotalMarketBuyVol -= s.prevBuyVol
		m.TotalMarketSellVol -= s.prevSellVol
		m.TotalMarketVolume -= s.prevVolume
		m.TotalMarketTurnover -= s.prevTurnover
	}

	// --- Compute new contribution ---
	isUp := s.LTP > s.PrevClose && s.PrevClose > 0
	isDown := s.LTP < s.PrevClose && s.PrevClose > 0
	isAboveVWAP := vwap > 0 && s.LTP > vwap

	// --- Add new contribution ---
	if isUp {
		m.StocksUp++
	}
	if isDown {
		m.StocksDown++
	}
	if !isUp && !isDown {
		m.StocksFlat++
	}
	if isAboveVWAP {
		m.StocksAboveVWAP++
	}
	m.TotalMarketBuyVol += s.CumulativeBuyVol
	m.TotalMarketSellVol += s.CumulativeSellVol
	m.TotalMarketVolume += s.CumulativeVolume
	m.TotalMarketTurnover += s.CumulativeTurnover

	// --- Save for next delta ---
	s.prevRegistered = true
	s.prevWasUp = isUp
	s.prevWasDown = isDown
	s.prevWasAboveVWAP = isAboveVWAP
	s.prevBuyVol = s.CumulativeBuyVol
	s.prevSellVol = s.CumulativeSellVol
	s.prevVolume = s.CumulativeVolume
	s.prevTurnover = s.CumulativeTurnover
}
