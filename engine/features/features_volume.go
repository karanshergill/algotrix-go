package features

// RegisterVolumeFeatures registers the 6 volume-category features (tick-triggered).
func RegisterVolumeFeatures(r *Registry) {
	r.Register(FeatureDef{
		Name: "volume_spike_z", Version: 1, Category: "volume",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			slot := timeToSlot(s.LastTickTS)
			b, ok := s.VolumeSlot[slot]
			return ok && b.StdDev > 0 && b.Samples >= 5
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			slot := timeToSlot(s.LastTickTS)
			b := s.VolumeSlot[slot]
			return (float64(s.Volume5m.Sum()) - b.Mean) / b.StdDev
		},
	})

	r.Register(FeatureDef{
		Name: "buy_pressure", Version: 1, Category: "volume",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.CumulativeBuyVol+s.CumulativeSellVol > 0
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			total := s.CumulativeBuyVol + s.CumulativeSellVol
			return float64(s.CumulativeBuyVol) / float64(total)
		},
	})

	r.Register(FeatureDef{
		Name: "buy_pressure_5m", Version: 1, Category: "volume",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			return s.BuyVol5m.Sum()+s.SellVol5m.Sum() > 0
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			buy := s.BuyVol5m.Sum()
			total := buy + s.SellVol5m.Sum()
			return float64(buy) / float64(total)
		},
	})

	r.Register(FeatureDef{
		Name: "update_intensity", Version: 1, Category: "volume",
		Trigger: TriggerTick,
		Ready:   func(s *StockState, m *MarketState) bool { return true },
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return float64(s.Updates1m.Sum())
		},
	})

	r.Register(FeatureDef{
		Name: "volume_spike_ratio", Version: 1, Category: "volume",
		Trigger: TriggerTick,
		Ready: func(s *StockState, m *MarketState) bool {
			if !s.CurrentSlotSet {
				return false
			}
			b, ok := s.VolumeSlot[s.CurrentSlot]
			return ok && b.Mean >= 10000 && b.Samples >= 5
		},
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			b := s.VolumeSlot[s.CurrentSlot]
			if s.CurrentSlotVol <= 0 {
				return 0
			}
			return float64(s.CurrentSlotVol) / b.Mean
		},
	})

	r.Register(FeatureDef{
		Name: "classified_volume_5m", Version: 1, Category: "volume",
		Trigger: TriggerTick,
		Ready:   func(s *StockState, m *MarketState) bool { return true },
		Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
			return float64(s.BuyVol5m.Sum() + s.SellVol5m.Sum())
		},
	})
}
