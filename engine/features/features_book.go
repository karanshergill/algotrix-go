package features

// RegisterBookFeatures registers the 3 book-category features (depth-triggered).
func RegisterBookFeatures(r *Registry) {
	r.Register(FeatureDef{
		Name: "book_imbalance", Version: 1, Category: "book",
		Trigger: TriggerDepth,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.HasDepth && s.BookImbalance60s != nil
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return s.BookImbalance60s.Avg(s.LastDepthTS, 0.5)
		},
	})

	r.Register(FeatureDef{
		Name: "book_imbalance_weighted", Version: 1, Category: "book",
		Trigger: TriggerDepth,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.HasDepth
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			weights := [5]float64{5, 3, 2, 1, 0.5}
			var bid, ask float64
			for i := 0; i < 5; i++ {
				bid += float64(s.BidQtys[i]) * weights[i]
				ask += float64(s.AskQtys[i]) * weights[i]
			}
			total := bid + ask
			if total == 0 {
				return 0.5
			}
			return bid / total
		},
	})

	r.Register(FeatureDef{
		Name: "spread_bps", Version: 1, Category: "book",
		Trigger: TriggerDepth,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.HasDepth && s.BidPrices[0] > 0 && s.AskPrices[0] > s.BidPrices[0]
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return (s.AskPrices[0] - s.BidPrices[0]) / s.BidPrices[0] * 10000
		},
	})
}
