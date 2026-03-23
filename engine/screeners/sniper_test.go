package screeners

import (
	"testing"
	"time"
)

func sniperPassingContext() *TickContext {
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

func TestSniperAllPass(t *testing.T) {
	scr := NewSniperScreener()
	sig := scr.Evaluate(sniperPassingContext())
	if sig == nil {
		t.Fatal("expected signal, got nil")
	}
	if sig.SignalType != SignalBuy {
		t.Errorf("expected BUY, got %s", sig.SignalType)
	}
	if sig.ScreenerName != "sniper" {
		t.Errorf("expected sniper, got %s", sig.ScreenerName)
	}
}

func TestSniperBeforeTime(t *testing.T) {
	scr := NewSniperScreener()
	ctx := sniperPassingContext()
	ctx.TickTime = time.Date(2025, 1, 6, 9, 30, 0, 0, time.FixedZone("IST", 5*3600+30*60))
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil signal before 10:00 IST")
	}
}

func TestSniperBelowVWAP(t *testing.T) {
	scr := NewSniperScreener()
	ctx := sniperPassingContext()
	ctx.Features["vwap_dist_bps"] = -10
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil signal when below VWAP")
	}
}

func TestSniperHighExhaustion(t *testing.T) {
	scr := NewSniperScreener()
	ctx := sniperPassingContext()
	ctx.Features["exhaustion"] = 0.5
	sig := scr.Evaluate(ctx)
	if sig != nil {
		t.Fatal("expected nil signal when exhaustion >= 0.5")
	}
}
