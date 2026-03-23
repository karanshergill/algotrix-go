package screeners

// BreakoutScreener fires on 2-session high crossover with confirmation filters.
type BreakoutScreener struct {
	Thresholds          map[string]float64 // ISIN → 2-session high
	RequireVolSpike     float64
	MaxExhaustion       float64
	RequireAboveVWAP    bool
	MaxRejectionWickPct float64
}

// NewBreakoutScreener creates a BreakoutScreener with thresholds loaded from DB.
func NewBreakoutScreener(thresholds map[string]float64) *BreakoutScreener {
	return &BreakoutScreener{
		Thresholds:          thresholds,
		RequireVolSpike:     1.5,
		MaxExhaustion:       0.75,
		RequireAboveVWAP:    true,
		MaxRejectionWickPct: 2.0,
	}
}

func (s *BreakoutScreener) Name() string { return "two_session_high_breakout" }

func (s *BreakoutScreener) Evaluate(ctx *TickContext) *Signal {
	f := ctx.Features
	if f == nil {
		return nil
	}

	// Filter 1: threshold exists for this ISIN
	threshold, exists := s.Thresholds[ctx.ISIN]
	if !exists {
		return nil
	}

	// Filter 2: crossover detection — PrevLTP must be > 0 (not first tick)
	if ctx.PrevLTP <= 0 {
		return nil
	}
	if !(ctx.PrevLTP <= threshold && ctx.LTP > threshold) {
		return nil
	}

	// Filter 3: market regime — bullish (Nifty above prev close)
	if ctx.Market.NiftyLTP <= ctx.Market.NiftyPrevClose {
		return nil
	}

	// Filter 4: exhaustion
	exhaustion, ok := f["exhaustion"]
	if !ok || exhaustion >= s.MaxExhaustion {
		return nil
	}

	// Filter 5: above VWAP
	vwapDist, ok := f["vwap_dist_bps"]
	if !ok || vwapDist <= 0 {
		return nil
	}

	// Filter 6: volume spike
	spikeRatio, ok := f["volume_spike_ratio"]
	if !ok || spikeRatio < s.RequireVolSpike {
		return nil
	}

	percentAbove := (ctx.LTP - threshold) / threshold * 100

	return &Signal{
		ScreenerName:   s.Name(),
		SignalType:     SignalBreakout,
		PercentAbove:   percentAbove,
		ThresholdPrice: threshold,
		Metadata: map[string]interface{}{
			"screener":       s.Name(),
			"threshold_price": threshold,
			"percent_above":  percentAbove,
			"volume_spike":   spikeRatio,
			"exhaustion":     exhaustion,
			"vwap_dist_bps":  vwapDist,
		},
	}
}

func (s *BreakoutScreener) Reset() {}
