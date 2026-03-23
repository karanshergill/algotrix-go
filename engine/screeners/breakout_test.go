package screeners

import (
	"testing"
	"time"
)

func breakoutPassingContext() *TickContext {
	return &TickContext{
		ISIN:   "INE001A01036",
		Symbol: "TESTCO",
		LTP:    1010.0,
		Features: map[string]float64{
			"exhaustion":         0.3,
			"vwap_dist_bps":     50,
			"volume_spike_ratio": 2.0,
		},
		Market:   MarketContext{NiftyLTP: 22000, NiftyPrevClose: 21900},
		TickTime: time.Date(2025, 1, 6, 10, 30, 0, 0, time.FixedZone("IST", 5*3600+30*60)),
		PrevLTP:  995.0, // below threshold
	}
}

func TestBreakoutCrossover(t *testing.T) {
	thresholds := map[string]float64{"INE001A01036": 1000.0}
	scr := NewBreakoutScreener(thresholds)
	ctx := breakoutPassingContext()
	// PrevLTP 995 <= 1000, LTP 1010 > 1000 → crossover
	sig := scr.Evaluate(ctx)
	if sig == nil {
		t.Fatal("expected breakout signal on crossover")
	}
	if sig.SignalType != SignalBreakout {
		t.Errorf("expected BREAKOUT, got %s", sig.SignalType)
	}
	if sig.ThresholdPrice != 1000.0 {
		t.Errorf("expected threshold 1000, got %f", sig.ThresholdPrice)
	}
}

func TestBreakoutNoCrossover(t *testing.T) {
	thresholds := map[string]float64{"INE001A01036": 1000.0}
	scr := NewBreakoutScreener(thresholds)
	ctx := breakoutPassingContext()
	ctx.PrevLTP = 1005.0 // already above threshold
	ctx.LTP = 1010.0
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil when both PrevLTP and LTP are above threshold")
	}
}

func TestBreakoutFirstTick(t *testing.T) {
	thresholds := map[string]float64{"INE001A01036": 1000.0}
	scr := NewBreakoutScreener(thresholds)
	ctx := breakoutPassingContext()
	ctx.PrevLTP = 0 // first tick
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil on first tick (PrevLTP == 0)")
	}
}

func TestBreakoutNoThreshold(t *testing.T) {
	thresholds := map[string]float64{} // empty
	scr := NewBreakoutScreener(thresholds)
	ctx := breakoutPassingContext()
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil when ISIN not in thresholds")
	}
}
