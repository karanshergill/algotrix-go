package screeners

// TridentScreener is a refined BUY screener with VWAP ceiling, hour reject, and spike cap.
type TridentScreener struct {
	MinSpikeRatio    float64
	MaxSpikeRatio    float64
	MinBuyRatio      float64
	MinTotalVolume   float64
	MinGapPct        float64
	MaxGapPct        float64
	MaxExhaustion    float64
	MinBookImbalance float64
	MaxVWAPDistBps   float64
	RejectHours      map[int]bool
}

// NewTridentScreener creates a TridentScreener with exact v2 thresholds.
func NewTridentScreener() *TridentScreener {
	return &TridentScreener{
		MinSpikeRatio:    2.0,
		MaxSpikeRatio:    5.0,
		MinBuyRatio:      0.65,
		MinTotalVolume:   5000,
		MinGapPct:        0.1,
		MaxGapPct:        3.0,
		MaxExhaustion:    0.5,
		MinBookImbalance: 0.55,
		MaxVWAPDistBps:   150,
		RejectHours:      map[int]bool{11: true},
	}
}

func (s *TridentScreener) Name() string { return "trident" }

func (s *TridentScreener) Evaluate(ctx *TickContext) *Signal {
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

	// Filter 3: volume spike ratio (min)
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

	// Filter 6: above VWAP
	vwapDist, ok := f["vwap_dist_bps"]
	if !ok || vwapDist <= 0 {
		return nil
	}

	// Filter 7: exhaustion
	exhaustion, ok := f["exhaustion"]
	if !ok || exhaustion >= s.MaxExhaustion {
		return nil
	}

	// Filter 8: book imbalance
	bookImbalance, ok := f["book_imbalance"]
	if !ok || bookImbalance < s.MinBookImbalance {
		return nil
	}

	// Filter 9: VWAP not too far above (ceiling)
	if vwapDist > s.MaxVWAPDistBps {
		return nil
	}

	// Filter 10: reject hours
	if s.RejectHours[ctx.TickTime.Hour()] {
		return nil
	}

	// Filter 11: reject abnormal spikes
	if spikeRatio >= s.MaxSpikeRatio {
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
			"exit_params": map[string]interface{}{
				"target_pct":           1.0,
				"hard_stop_pct":        2.0,
				"trail_activation_pct": 0.3,
				"trail_distance_pct":   0.35,
				"sqoff_time":           "15:20",
			},
		},
	}
}

func (s *TridentScreener) Reset() {}
