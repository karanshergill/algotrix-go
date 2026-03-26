package features

import "math"

// RegisterPriceFeatures registers the 5 price-category features (tick-triggered).
func RegisterPriceFeatures(r *Registry) {
	r.Register(FeatureDef{
		Name: "vwap", Version: 1, Category: "price",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.ExchVWAP > 0 || s.CumulativeVolume > 0
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			if s.ExchVWAP > 0 {
				return s.ExchVWAP
			}
			return s.CumulativeTurnover / float64(s.CumulativeVolume)
		},
	})

	r.Register(FeatureDef{
		Name: "vwap_dist_bps", Version: 1, Category: "price",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.ExchVWAP > 0 || s.CumulativeVolume > 0
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			vwap := s.ExchVWAP
			if vwap == 0 {
				vwap = s.CumulativeTurnover / float64(s.CumulativeVolume)
			}
			if vwap == 0 {
				return 0
			}
			return (s.LTP - vwap) / vwap * 10000
		},
	})

	r.Register(FeatureDef{
		Name: "change_pct", Version: 1, Category: "price",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.PrevClose > 0
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return (s.LTP - s.PrevClose) / s.PrevClose * 100
		},
	})

	r.Register(FeatureDef{
		Name: "day_range_pct", Version: 1, Category: "price",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.PrevClose > 0 && s.DayHigh > 0
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return (s.DayHigh - s.DayLow) / s.PrevClose * 100
		},
	})

	r.Register(FeatureDef{
		Name: "exhaustion", Version: 1, Category: "price",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.ATR14d > 0 && s.PrevClose > 0
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return math.Abs(s.LTP-s.PrevClose) / s.ATR14d
		},
	})
}
