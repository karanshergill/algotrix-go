package screeners

import (
	"testing"
	"time"
)

func thinPassingContext() *TickContext {
	return &TickContext{
		ISIN:   "INE002A01036",
		Symbol: "SMALLCO",
		LTP:    500.0,
		Features: map[string]float64{
			"change_pct":           1.5,
			"volume_spike_ratio":   2.0,
			"buy_pressure_5m":      0.65,
			"classified_volume_5m": 1000,
			"book_imbalance":       0.65,
		},
		Market:   MarketContext{NiftyLTP: 22000, NiftyPrevClose: 21900},
		TickTime: time.Date(2025, 1, 6, 10, 30, 0, 0, time.FixedZone("IST", 5*3600+30*60)),
		PrevLTP:  499.0,
	}
}

func TestThinMomentumConfirmingTicks(t *testing.T) {
	scr := NewThinMomentumScreener()
	ctx := thinPassingContext()

	// Warmup: first 19 ticks return nil (tickCount 1..19, all < 20)
	for i := 0; i < 19; i++ {
		if sig := scr.Evaluate(ctx); sig != nil {
			t.Fatalf("expected nil during warmup tick %d", i)
		}
	}

	// Tick 20 passes warmup, starts confirming (count=1) — still nil
	if sig := scr.Evaluate(ctx); sig != nil {
		t.Fatal("expected nil on confirming tick 1")
	}
	// Confirming tick 2 — still nil
	if sig := scr.Evaluate(ctx); sig != nil {
		t.Fatal("expected nil on confirming tick 2")
	}

	// Confirming tick 3: should fire
	sig := scr.Evaluate(ctx)
	if sig == nil {
		t.Fatal("expected signal on confirming tick 3")
	}
	if sig.SignalType != SignalAlert {
		t.Errorf("expected ALERT, got %s", sig.SignalType)
	}
}

func TestThinMomentumWarmup(t *testing.T) {
	scr := NewThinMomentumScreener()
	ctx := thinPassingContext()

	// First 19 ticks should all return nil (warmup)
	for i := 0; i < 19; i++ {
		if sig := scr.Evaluate(ctx); sig != nil {
			t.Fatalf("expected nil during warmup tick %d", i)
		}
	}
}

func TestThinMomentumPriceRange(t *testing.T) {
	scr := NewThinMomentumScreener()

	// Too cheap
	ctx := thinPassingContext()
	ctx.LTP = 50
	// Burn through warmup
	for i := 0; i < 25; i++ {
		scr.Evaluate(ctx)
	}
	// Should still be nil due to price
	if sig := scr.Evaluate(ctx); sig != nil {
		t.Fatal("expected nil for price < 100")
	}

	// Too expensive — use a different ISIN to avoid state contamination
	scr2 := NewThinMomentumScreener()
	ctx2 := thinPassingContext()
	ctx2.ISIN = "INE003A01036"
	ctx2.LTP = 2500
	for i := 0; i < 25; i++ {
		scr2.Evaluate(ctx2)
	}
	if sig := scr2.Evaluate(ctx2); sig != nil {
		t.Fatal("expected nil for price > 2000")
	}
}

func TestThinMomentumSpikeOrBook(t *testing.T) {
	scr := NewThinMomentumScreener()
	ctx := thinPassingContext()
	// Low spike AND low book — should fail the OR check
	ctx.Features["volume_spike_ratio"] = 1.0
	ctx.Features["book_imbalance"] = 0.65 // above MinBookBuyRatio but below BookOverride

	// Burn through warmup + confirming
	for i := 0; i < 30; i++ {
		scr.Evaluate(ctx)
	}
	if sig := scr.Evaluate(ctx); sig != nil {
		t.Fatal("expected nil when spike < 1.5 AND book_imbalance < 0.70")
	}

	// Now set book override — should pass after confirming ticks accumulate
	scr2 := NewThinMomentumScreener()
	ctx2 := thinPassingContext()
	ctx2.Features["volume_spike_ratio"] = 1.0 // below spike threshold
	ctx2.Features["book_imbalance"] = 0.75    // above book override
	for i := 0; i < 23; i++ {
		scr2.Evaluate(ctx2)
	}
	sig := scr2.Evaluate(ctx2)
	if sig == nil {
		t.Fatal("expected signal when book_imbalance >= 0.70 overrides spike")
	}
}
