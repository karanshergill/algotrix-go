package features

import (
	"testing"
	"context"
	"time"

	"github.com/karanshergill/algotrix-go/feed"
)

func TestFeedAdapter_AdaptTick(t *testing.T) {
	engine := NewFeatureEngine(&EngineConfig{
		TickBuffer:  100,
		DepthBuffer: 100,
		GuardConfig: &GuardConfig{
			MaxPriceJumpPct:  20.0,
			MinLTP:           0.01,
			AllowVolumeReset: true,
		},
	})

	engine.RegisterStock("INE001", "RELIANCE", "")
	adapter := NewFeedAdapter(engine, nil)
	ts := time.Now()
	adapter.AdaptTick(feed.TickData{
		Symbol:         "RELIANCE",
		ISIN:           "INE001",
		LTP:            2500.0,
		Volume:         1000,
		TS:             ts,
		OpenPrice:      2490.0,
		HighPrice:      2510.0,
		LowPrice:       2480.0,
		PrevClosePrice: 2450.0,
		Change:         50.0,
		ChangePct:      2.04,
		TotBuyQty:      12000,
		TotSellQty:     9000,
		BidPrice:       2499.5,
		AskPrice:       2500.5,
		BidSize:        400,
		AskSize:        500,
		AvgTradePrice:  2497.25,
		LastTradedQty:  75,
		LastTradedTime: 1711443600,
		ExchFeedTime:   1711443601,
		OI:             3456,
		YearHigh:       3100.0,
		YearLow:        1900.0,
		LowerCircuit:   2200.0,
		UpperCircuit:   2700.0,
	})

	select {
	case ev := <-engine.tickCh:
		if ev.ISIN != "INE001" || ev.Symbol != "RELIANCE" {
			t.Fatalf("unexpected tick identity: %+v", ev)
		}
		if ev.LTP != 2500.0 || ev.Volume != 1000 || !ev.TS.Equal(ts) {
			t.Fatalf("base tick fields not mapped: %+v", ev)
		}
		if ev.OpenPrice != 2490.0 || ev.HighPrice != 2510.0 || ev.LowPrice != 2480.0 {
			t.Fatalf("ohlc fields not mapped: %+v", ev)
		}
		if ev.PrevClosePrice != 2450.0 || ev.Change != 50.0 || ev.ChangePct != 2.04 {
			t.Fatalf("change fields not mapped: %+v", ev)
		}
		if ev.TotBuyQty != 12000 || ev.TotSellQty != 9000 {
			t.Fatalf("book totals not mapped: %+v", ev)
		}
		if ev.BidPrice != 2499.5 || ev.AskPrice != 2500.5 || ev.BidSize != 400 || ev.AskSize != 500 {
			t.Fatalf("level-1 fields not mapped: %+v", ev)
		}
		if ev.AvgTradePrice != 2497.25 || ev.LastTradedQty != 75 || ev.OI != 3456 {
			t.Fatalf("trade fields not mapped: %+v", ev)
		}
		if ev.LastTradedTime != 1711443600 || ev.ExchFeedTime != 1711443601 {
			t.Fatalf("exchange timestamps not mapped: %+v", ev)
		}
		if ev.YearHigh != 3100.0 || ev.YearLow != 1900.0 || ev.LowerCircuit != 2200.0 || ev.UpperCircuit != 2700.0 {
			t.Fatalf("range/circuit fields not mapped: %+v", ev)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("tick was not enqueued")
	}
}

func TestFeedAdapter_AdaptDepth(t *testing.T) {
	engine := NewFeatureEngine(&EngineConfig{
		TickBuffer:  100,
		DepthBuffer: 100,
		GuardConfig: &GuardConfig{
			MaxPriceJumpPct:  20.0,
			MinLTP:           0.01,
			AllowVolumeReset: true,
		},
	})

	engine.RegisterStock("INE001", "RELIANCE", "")
	engine.session.SessionStart(time.Now())

	processed := make(chan string, 1)
	engine.SetOnDepth(func(isin string) {
		processed <- isin
	})

	adapter := NewFeedAdapter(engine, nil)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go engine.Run(ctx)

	bids := []DepthLevel{{Price: 2499.0, Qty: 100}, {Price: 2498.0, Qty: 200}}
	asks := []DepthLevel{{Price: 2501.0, Qty: 150}, {Price: 2502.0, Qty: 250}}
	adapter.AdaptDepth("INE001", bids, asks, time.Now())

	select {
	case isin := <-processed:
		if isin != "INE001" {
			t.Fatalf("expected INE001, got %s", isin)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("depth not processed within timeout")
	}

	s := engine.Stock("INE001")
	if !s.HasDepth {
		t.Error("expected HasDepth=true")
	}
	if s.BidPrices[0] != 2499.0 {
		t.Errorf("expected bid[0]=2499, got %f", s.BidPrices[0])
	}
}

func TestFeedAdapter_NonBlocking(t *testing.T) {
	engine := NewFeatureEngine(&EngineConfig{
		TickBuffer:  1,
		DepthBuffer: 1,
		GuardConfig: &GuardConfig{
			MaxPriceJumpPct:  20.0,
			MinLTP:           0.01,
			AllowVolumeReset: true,
		},
	})

	engine.RegisterStock("INE001", "RELIANCE", "")

	adapter := NewFeedAdapter(engine, nil)

	// Fill the tick channel (don't start engine — channel stays full)
	adapter.AdaptTick(feed.TickData{Symbol: "RELIANCE", ISIN: "INE001", LTP: 100.0, Volume: 100, TS: time.Now()})

	// This must not block
	done := make(chan struct{})
	go func() {
		adapter.AdaptTick(feed.TickData{Symbol: "RELIANCE", ISIN: "INE001", LTP: 101.0, Volume: 200, TS: time.Now()})
		close(done)
	}()

	select {
	case <-done:
		// OK — didn't block
	case <-time.After(1 * time.Second):
		t.Fatal("AdaptTick blocked when channel was full")
	}

	// Same for depth
	adapter.AdaptDepth("INE001", nil, nil, time.Now())

	done2 := make(chan struct{})
	go func() {
		adapter.AdaptDepth("INE001", nil, nil, time.Now())
		close(done2)
	}()

	select {
	case <-done2:
		// OK
	case <-time.After(1 * time.Second):
		t.Fatal("AdaptDepth blocked when channel was full")
	}
}
