package features

import "time"

// ---------------------------------------------------------------------------
// Immutable snapshots — copy-on-write for safe concurrent reads.
// The event loop atomically swaps the snapshot pointer after each update.
// REST and hub consumers read snapshots without locks.
// ---------------------------------------------------------------------------

// EngineSnapshot is an immutable point-in-time view of engine state.
type EngineSnapshot struct {
	Stocks  map[string]StockSnapshot
	Market  MarketSnapshot
	Sectors map[string]SectorSnapshot
	TS      time.Time
}

// StockSnapshot is an immutable view of one stock's state and features.
type StockSnapshot struct {
	ISIN     string
	Symbol   string
	LTP      float64
	Features map[string]float64
	Quality  QualityFlags
}

// MarketSnapshot is an immutable copy of MarketState fields.
type MarketSnapshot struct {
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

// SectorSnapshot is an immutable copy of SectorState fields.
type SectorSnapshot struct {
	Name            string
	StocksUp        int
	StocksDown      int
	StocksAboveVWAP int
	TotalStocks     int
	TotalBuyVol     int64
	TotalSellVol    int64
	TotalVolume     int64
	TotalTurnover   float64
}

// NewEngineSnapshot creates an EngineSnapshot with initialized empty maps.
func NewEngineSnapshot() *EngineSnapshot {
	return &EngineSnapshot{
		Stocks:  make(map[string]StockSnapshot),
		Sectors: make(map[string]SectorSnapshot),
	}
}

// Clone returns a deep copy of the snapshot. All maps are copied.
func (s *EngineSnapshot) Clone() *EngineSnapshot {
	c := &EngineSnapshot{
		Stocks:  make(map[string]StockSnapshot, len(s.Stocks)),
		Market:  s.Market,
		Sectors: make(map[string]SectorSnapshot, len(s.Sectors)),
		TS:      s.TS,
	}
	for k, v := range s.Stocks {
		// Deep-copy the Features map within each StockSnapshot
		feat := make(map[string]float64, len(v.Features))
		for fk, fv := range v.Features {
			feat[fk] = fv
		}
		v.Features = feat
		c.Stocks[k] = v
	}
	for k, v := range s.Sectors {
		c.Sectors[k] = v
	}
	return c
}

// UpdateStock sets or replaces the snapshot for one stock by ISIN.
func (s *EngineSnapshot) UpdateStock(isin string, snap StockSnapshot) {
	s.Stocks[isin] = snap
}

// MarketSnapshotFrom creates a MarketSnapshot from the live MarketState.
func MarketSnapshotFrom(m *MarketState) MarketSnapshot {
	return MarketSnapshot{
		TotalStocks:         m.TotalStocks,
		StocksUp:            m.StocksUp,
		StocksDown:          m.StocksDown,
		StocksFlat:          m.StocksFlat,
		StocksAboveVWAP:     m.StocksAboveVWAP,
		TotalMarketBuyVol:   m.TotalMarketBuyVol,
		TotalMarketSellVol:  m.TotalMarketSellVol,
		TotalMarketVolume:   m.TotalMarketVolume,
		TotalMarketTurnover: m.TotalMarketTurnover,
		NiftyLTP:            m.NiftyLTP,
		NiftyPrevClose:      m.NiftyPrevClose,
		NiftyDayHigh:        m.NiftyDayHigh,
		NiftyDayLow:         m.NiftyDayLow,
	}
}

// SectorSnapshotFrom creates a SectorSnapshot from the live SectorState.
func SectorSnapshotFrom(s *SectorState) SectorSnapshot {
	return SectorSnapshot{
		Name:            s.Name,
		StocksUp:        s.StocksUp,
		StocksDown:      s.StocksDown,
		StocksAboveVWAP: s.StocksAboveVWAP,
		TotalStocks:     s.TotalStocks,
		TotalBuyVol:     s.TotalBuyVol,
		TotalSellVol:    s.TotalSellVol,
		TotalVolume:     s.TotalVolume,
		TotalTurnover:   s.TotalTurnover,
	}
}
