package screeners

import (
	"testing"
	"time"
)

func tridentPassingContext() *TickContext {
	return &TickContext{
		ISIN:   "INE001A01036",
		Symbol: "TESTCO",
		LTP:    105.0,
		Features: map[string]float64{
			"change_pct":           1.5,
			"volume_spike_ratio":   3.0,
			"buy_pressure_5m":      0.70,
			"classified_volume_5m": 10000,
			"vwap_dist_bps":        50,
			"exhaustion":           0.3,
			"book_imbalance":       0.60,
		},
		Market:   MarketContext{NiftyLTP: 22000, NiftyPrevClose: 21900},
		TickTime: time.Date(2025, 1, 6, 10, 30, 0, 0, time.FixedZone("IST", 5*3600+30*60)),
		PrevLTP:  104.0,
	}
}

func TestTridentAllPass(t *testing.T) {
	scr := NewTridentScreener()
	sig := scr.Evaluate(tridentPassingContext())
	if sig == nil {
		t.Fatal("expected signal, got nil")
	}
	if sig.SignalType != SignalBuy {
		t.Errorf("expected BUY, got %s", sig.SignalType)
	}
	if sig.ScreenerName != "trident" {
		t.Errorf("expected trident, got %s", sig.ScreenerName)
	}
	// Verify exit_params in metadata
	ep, ok := sig.Metadata["exit_params"].(map[string]interface{})
	if !ok {
		t.Fatal("expected exit_params in metadata")
	}
	if ep["target_pct"] != 1.0 {
		t.Errorf("expected target_pct 1.0, got %v", ep["target_pct"])
	}
}

func TestTridentRejectHour11(t *testing.T) {
	scr := NewTridentScreener()
	ctx := tridentPassingContext()
	ctx.TickTime = time.Date(2025, 1, 6, 11, 15, 0, 0, time.FixedZone("IST", 5*3600+30*60))
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil signal during hour 11")
	}
}

func TestTridentVWAPTooHigh(t *testing.T) {
	scr := NewTridentScreener()
	ctx := tridentPassingContext()
	ctx.Features["vwap_dist_bps"] = 200
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil signal when VWAP dist > 150 bps")
	}
}

func TestTridentSpikeTooBig(t *testing.T) {
	scr := NewTridentScreener()
	ctx := tridentPassingContext()
	ctx.Features["volume_spike_ratio"] = 5.0
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil signal when spike ratio >= 5.0")
	}
}
