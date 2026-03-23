package features

import "time"

// QualityFlags indicates data completeness and freshness for a stock.
type QualityFlags struct {
	Partial         bool  // UpdateCount < 20 — features still warming up
	BaselineMissing bool  // ATR14d == 0 or VolumeSlot empty — baselines not loaded
	DepthStaleMs    int64 // ms since last depth event (0 if no depth yet)
	TickStaleMs     int64 // ms since last tick (0 if no tick yet)
}

// ComputeQuality evaluates data quality flags for a stock at the given time.
func ComputeQuality(s *StockState, now time.Time) QualityFlags {
	var depthStale, tickStale int64
	if !s.LastDepthTS.IsZero() {
		depthStale = now.Sub(s.LastDepthTS).Milliseconds()
	}
	if !s.LastTickTS.IsZero() {
		tickStale = now.Sub(s.LastTickTS).Milliseconds()
	}

	return QualityFlags{
		Partial:         s.UpdateCount < 20,
		BaselineMissing: s.ATR14d == 0 || len(s.VolumeSlot) == 0,
		DepthStaleMs:    depthStale,
		TickStaleMs:     tickStale,
	}
}
