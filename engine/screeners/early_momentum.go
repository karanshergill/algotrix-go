package screeners

// EarlyMomentumScreener detects early momentum via volume spike + buy pressure.
type EarlyMomentumScreener struct {
	MinSpikeRatio  float64
	MinBuyRatio    float64
	MinTotalVolume float64
	MinChangePct   float64
	MaxChangePct   float64
}

// NewEarlyMomentumScreener creates an EarlyMomentumScreener with v2 thresholds.
func NewEarlyMomentumScreener() *EarlyMomentumScreener {
	return &EarlyMomentumScreener{
		MinSpikeRatio:  2.0,
		MinBuyRatio:    0.65,
		MinTotalVolume: 5000,
		MinChangePct:   0.5,
		MaxChangePct:   3.0,
	}
}

func (s *EarlyMomentumScreener) Name() string { return "early_momentum" }

func (s *EarlyMomentumScreener) Evaluate(ctx *TickContext) *Signal {
	f := ctx.Features
	if f == nil {
		return nil
	}

	// Filter 1: change_pct in range
	changePct, ok := f["change_pct"]
	if !ok || changePct < s.MinChangePct || changePct > s.MaxChangePct {
		return nil
	}

	// Filter 2: volume spike ratio
	spikeRatio, ok := f["volume_spike_ratio"]
	if !ok || spikeRatio < s.MinSpikeRatio {
		return nil
	}

	// Filter 3: buy pressure (5m rolling)
	buyRatio, ok := f["buy_pressure_5m"]
	if !ok || buyRatio < s.MinBuyRatio {
		return nil
	}

	// Filter 4: classified volume minimum
	classVol, ok := f["classified_volume_5m"]
	if !ok || classVol < s.MinTotalVolume {
		return nil
	}

	return &Signal{
		ScreenerName:   s.Name(),
		SignalType:     SignalAlert,
		PercentAbove:   changePct,
		ThresholdPrice: ctx.LTP / (1 + changePct/100), // approximate prev close
		Metadata: map[string]interface{}{
			"screener":           s.Name(),
			"volume_spike_ratio": spikeRatio,
			"buy_ratio":          buyRatio,
			"classified_volume":  classVol,
			"change_pct":         changePct,
		},
	}
}

func (s *EarlyMomentumScreener) Reset() {}
