package screeners

// SniperScreener detects high-conviction BUY setups with VWAP/exhaustion/book guards.
type SniperScreener struct {
	MinSpikeRatio    float64
	MinBuyRatio      float64
	MinTotalVolume   float64
	MinGapPct        float64
	MaxGapPct        float64
	MaxExhaustion    float64
	MinBookImbalance float64
}

// NewSniperScreener creates a SniperScreener with exact v2 thresholds.
func NewSniperScreener() *SniperScreener {
	return &SniperScreener{
		MinSpikeRatio:    2.0,
		MinBuyRatio:      0.65,
		MinTotalVolume:   5000,
		MinGapPct:        0.1,
		MaxGapPct:        3.0,
		MaxExhaustion:    0.5,
		MinBookImbalance: 0.55,
	}
}

func (s *SniperScreener) Name() string { return "sniper" }

func (s *SniperScreener) Evaluate(ctx *TickContext) *Signal {
	f := ctx.Features
	if f == nil {
		return nil
	}

	// Filter 1: change_pct in range (0.1–3.0)
	changePct, ok := f["change_pct"]
	if !ok || changePct < s.MinGapPct || changePct > s.MaxGapPct {
		return nil
	}

	// Filter 2: time >= 10:00 IST
	if ctx.TickTime.Hour() < 10 {
		return nil
	}

	// Filter 3: volume spike ratio
	spikeRatio, ok := f["volume_spike_ratio"]
	if !ok || spikeRatio < s.MinSpikeRatio {
		return nil
	}

	// Filter 4: buy pressure
	buyRatio, ok := f["buy_pressure_5m"]
	if !ok || buyRatio < s.MinBuyRatio {
		return nil
	}

	// Filter 5: classified volume minimum
	classVol, ok := f["classified_volume_5m"]
	if !ok || classVol < s.MinTotalVolume {
		return nil
	}

	// Filter 6: above VWAP (positive = above)
	vwapDist, ok := f["vwap_dist_bps"]
	if !ok || vwapDist <= 0 {
		return nil
	}

	// Filter 7: exhaustion below threshold
	exhaustion, ok := f["exhaustion"]
	if !ok || exhaustion >= s.MaxExhaustion {
		return nil
	}

	// Filter 8: book imbalance
	bookImbalance, ok := f["book_imbalance"]
	if !ok || bookImbalance < s.MinBookImbalance {
		return nil
	}

	return &Signal{
		ScreenerName:   s.Name(),
		SignalType:     SignalBuy,
		PercentAbove:   changePct,
		ThresholdPrice: ctx.LTP / (1 + changePct/100),
		Metadata: map[string]interface{}{
			"screener":           s.Name(),
			"volume_spike_ratio": spikeRatio,
			"buy_ratio":          buyRatio,
			"classified_volume":  classVol,
			"change_pct":         changePct,
			"vwap_dist_bps":      vwapDist,
			"exhaustion":         exhaustion,
			"book_imbalance":     bookImbalance,
		},
	}
}

func (s *SniperScreener) Reset() {}
