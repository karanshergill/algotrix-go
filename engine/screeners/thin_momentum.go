package screeners

// ThinMomentumScreener detects momentum in thin/small-cap stocks with warmup and confirmation.
type ThinMomentumScreener struct {
	MinSpikeRatio    float64
	MinBuyRatio      float64
	MinTotalVolume   float64
	MinChangePct     float64
	MaxChangePct     float64
	MinBookBuyRatio  float64
	MinPrice         float64
	MaxPrice         float64
	MinConfirming    int
	WarmupTicks      int
	BookOverride     float64 // book_imbalance threshold for spike override

	tickCount       map[string]int
	confirmingTicks map[string]int
}

// NewThinMomentumScreener creates a ThinMomentumScreener with exact v2 thresholds.
func NewThinMomentumScreener() *ThinMomentumScreener {
	return &ThinMomentumScreener{
		MinSpikeRatio:   1.5,
		MinBuyRatio:     0.60,
		MinTotalVolume:  500,
		MinChangePct:    0.5,
		MaxChangePct:    3.0,
		MinBookBuyRatio: 0.60,
		MinPrice:        100,
		MaxPrice:        2000,
		MinConfirming:   3,
		WarmupTicks:     20,
		BookOverride:    0.70,

		tickCount:       make(map[string]int),
		confirmingTicks: make(map[string]int),
	}
}

func (s *ThinMomentumScreener) Name() string { return "thin_momentum" }

func (s *ThinMomentumScreener) Evaluate(ctx *TickContext) *Signal {
	f := ctx.Features
	if f == nil {
		return nil
	}

	isin := ctx.ISIN

	// Increment tick count for warmup tracking
	s.tickCount[isin]++

	// Warmup: skip until enough ticks
	if s.tickCount[isin] < s.WarmupTicks {
		return nil
	}

	// Filter 1: price range
	if ctx.LTP < s.MinPrice || ctx.LTP > s.MaxPrice {
		s.confirmingTicks[isin] = 0
		return nil
	}

	// Filter 2: change_pct in range
	changePct, ok := f["change_pct"]
	if !ok || changePct < s.MinChangePct || changePct > s.MaxChangePct {
		s.confirmingTicks[isin] = 0
		return nil
	}

	// Filter 3: buy pressure
	buyRatio, ok := f["buy_pressure_5m"]
	if !ok || buyRatio < s.MinBuyRatio {
		s.confirmingTicks[isin] = 0
		return nil
	}

	// Filter 4: classified volume minimum
	classVol, ok := f["classified_volume_5m"]
	if !ok || classVol < s.MinTotalVolume {
		s.confirmingTicks[isin] = 0
		return nil
	}

	// Filter 5: spike OR book override
	spikeRatio := f["volume_spike_ratio"]
	bookImbalance := f["book_imbalance"]
	if spikeRatio < s.MinSpikeRatio && bookImbalance < s.BookOverride {
		s.confirmingTicks[isin] = 0
		return nil
	}

	// Filter 6: book imbalance minimum
	if bookImbalance < s.MinBookBuyRatio {
		s.confirmingTicks[isin] = 0
		return nil
	}

	// All conditions pass — increment confirming ticks
	s.confirmingTicks[isin]++
	if s.confirmingTicks[isin] < s.MinConfirming {
		return nil
	}

	return &Signal{
		ScreenerName:   s.Name(),
		SignalType:     SignalAlert,
		PercentAbove:   changePct,
		ThresholdPrice: ctx.LTP / (1 + changePct/100),
		Metadata: map[string]interface{}{
			"screener":           s.Name(),
			"volume_spike_ratio": spikeRatio,
			"buy_ratio":          buyRatio,
			"classified_volume":  classVol,
			"change_pct":         changePct,
			"book_imbalance":     bookImbalance,
			"confirming_ticks":   s.confirmingTicks[isin],
		},
	}
}

func (s *ThinMomentumScreener) Reset() {
	s.tickCount = make(map[string]int)
	s.confirmingTicks = make(map[string]int)
}
