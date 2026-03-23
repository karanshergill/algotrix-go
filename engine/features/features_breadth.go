package features

// RegisterBreadthFeatures registers the 3 breadth-category features (tick-triggered).
func RegisterBreadthFeatures(r *Registry) {
	r.Register(FeatureDef{
		Name: "breadth_ratio", Version: 1, Category: "breadth",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return m.StocksUp+m.StocksDown > 0
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			total := m.StocksUp + m.StocksDown
			return float64(m.StocksUp) / float64(total)
		},
	})

	r.Register(FeatureDef{
		Name: "vwap_breadth", Version: 1, Category: "breadth",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return m.TotalStocks > 0
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return float64(m.StocksAboveVWAP) / float64(m.TotalStocks)
		},
	})

	r.Register(FeatureDef{
		Name: "market_buy_pressure", Version: 1, Category: "breadth",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return m.TotalMarketBuyVol+m.TotalMarketSellVol > 0
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			total := m.TotalMarketBuyVol + m.TotalMarketSellVol
			return float64(m.TotalMarketBuyVol) / float64(total)
		},
	})
}
