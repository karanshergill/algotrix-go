package features

// RegisterSectorFeatures registers the 2 sector-category features (tick-triggered).
func RegisterSectorFeatures(r *Registry) {
	r.Register(FeatureDef{
		Name: "sector_breadth", Version: 1, Category: "sector",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.SectorID != ""
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			if sec == nil || sec.StocksUp+sec.StocksDown == 0 {
				return 0.5
			}
			total := sec.StocksUp + sec.StocksDown
			return float64(sec.StocksUp) / float64(total)
		},
	})

	r.Register(FeatureDef{
		Name: "sector_buy_pressure", Version: 1, Category: "sector",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.SectorID != ""
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			if sec == nil {
				return 0.5
			}
			total := sec.TotalBuyVol + sec.TotalSellVol
			if total == 0 {
				return 0.5
			}
			return float64(sec.TotalBuyVol) / float64(total)
		},
	})
}
