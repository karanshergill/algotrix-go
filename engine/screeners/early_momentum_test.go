package screeners

import (
	"testing"
	"time"

	"github.com/karanshergill/algotrix-go/features"
)

// helper to build a TickContext with all conditions passing.
func passingContext() *TickContext {
	return &TickContext{
		ISIN:   "INE001A01036",
		Symbol: "TESTCO",
		LTP:    105.0,
		Features: map[string]float64{
			"change_pct":          1.5,
			"volume_spike_ratio":  3.0,
			"buy_pressure_5m":     0.70,
			"classified_volume_5m": 10000,
		},
		Market:   MarketContext{NiftyLTP: 22000, NiftyPrevClose: 21900},
		TickTime: time.Now(),
		PrevLTP:  104.0,
	}
}

func TestEarlyMomentumAllPass(t *testing.T) {
	scr := NewEarlyMomentumScreener()
	ctx := passingContext()
	sig := scr.Evaluate(ctx)
	if sig == nil {
		t.Fatal("expected signal, got nil")
	}
	if sig.ScreenerName != "early_momentum" {
		t.Errorf("expected screener name early_momentum, got %s", sig.ScreenerName)
	}
	if sig.SignalType != SignalAlert {
		t.Errorf("expected ALERT, got %s", sig.SignalType)
	}
	if sig.PercentAbove != 1.5 {
		t.Errorf("expected PercentAbove 1.5, got %f", sig.PercentAbove)
	}
}

func TestEarlyMomentumLowVolume(t *testing.T) {
	scr := NewEarlyMomentumScreener()
	ctx := passingContext()
	ctx.Features["classified_volume_5m"] = 4999
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil signal for low classified volume")
	}
}

func TestEarlyMomentumChangePctOutOfRange(t *testing.T) {
	scr := NewEarlyMomentumScreener()

	// Too high
	ctx := passingContext()
	ctx.Features["change_pct"] = 3.1
	if sig := scr.Evaluate(ctx); sig != nil {
		t.Error("expected nil signal for change_pct > 3.0")
	}

	// Too low
	ctx = passingContext()
	ctx.Features["change_pct"] = 0.4
	if sig := scr.Evaluate(ctx); sig != nil {
		t.Error("expected nil signal for change_pct < 0.5")
	}
}

func TestEarlyMomentumLowSpike(t *testing.T) {
	scr := NewEarlyMomentumScreener()
	ctx := passingContext()
	ctx.Features["volume_spike_ratio"] = 1.9
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil signal for low spike ratio")
	}
}

func TestEarlyMomentumLowBuyRatio(t *testing.T) {
	scr := NewEarlyMomentumScreener()
	ctx := passingContext()
	ctx.Features["buy_pressure_5m"] = 0.64
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil signal for low buy ratio")
	}
}

func TestEarlyMomentumNilFeatures(t *testing.T) {
	scr := NewEarlyMomentumScreener()
	ctx := passingContext()
	ctx.Features = nil
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil signal for nil features")
	}
}

func TestEngineDedup(t *testing.T) {
	scr := NewEarlyMomentumScreener()
	eng := NewEngine([]Screener{scr}, nil)

	// Override sessionDate and market hours gate by setting it directly
	ist := time.Now().In(time.FixedZone("IST", 5*3600+30*60))
	eng.sessionDate = ist.Format("2006-01-02")

	stockSnap := &features.StockSnapshot{
		ISIN:   "INE001A01036",
		Symbol: "TESTCO",
		LTP:    105.0,
		Features: map[string]float64{
			"change_pct":          1.5,
			"volume_spike_ratio":  3.0,
			"buy_pressure_5m":     0.70,
			"classified_volume_5m": 10000,
		},
	}
	marketSnap := &features.MarketSnapshot{
		NiftyLTP:       22000,
		NiftyPrevClose: 21900,
	}

	// First tick — should fire
	sigs := eng.ProcessTick("INE001A01036", stockSnap, marketSnap)

	// Check if we're outside market hours (test may run anytime)
	hour, min := ist.Hour(), ist.Minute()
	marketMinute := hour*60 + min
	if marketMinute < 9*60+15 || marketMinute > 15*60+30 {
		// Outside market hours — engine gates all signals
		if len(sigs) != 0 {
			t.Fatal("expected no signals outside market hours")
		}
		t.Skip("skipping dedup test — outside IST market hours")
	}

	if len(sigs) != 1 {
		t.Fatalf("expected 1 signal on first tick, got %d", len(sigs))
	}

	// Second tick — same stock, same day → dedup
	sigs2 := eng.ProcessTick("INE001A01036", stockSnap, marketSnap)
	if len(sigs2) != 0 {
		t.Fatalf("expected 0 signals on second tick (dedup), got %d", len(sigs2))
	}
}
