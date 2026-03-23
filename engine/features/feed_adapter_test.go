package features

import (
	"context"
	"testing"
	"time"
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
	engine.session.SessionStart(time.Now())

	processed := make(chan string, 1)
	engine.SetOnTick(func(isin string) {
		processed <- isin
	})

	adapter := NewFeedAdapter(engine, nil)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go engine.Run(ctx)

	adapter.AdaptTick("RELIANCE", "INE001", 2500.0, 1000, time.Now())

	select {
	case isin := <-processed:
		if isin != "INE001" {
			t.Fatalf("expected INE001, got %s", isin)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("tick not processed within timeout")
	}

	s := engine.Stock("INE001")
	if s == nil {
		t.Fatal("stock not found")
	}
	if s.LTP != 2500.0 {
		t.Errorf("expected LTP=2500, got %f", s.LTP)
	}
	if s.CumulativeVolume != 1000 {
		t.Errorf("expected volume=1000, got %d", s.CumulativeVolume)
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
	adapter.AdaptTick("RELIANCE", "INE001", 100.0, 100, time.Now())

	// This must not block
	done := make(chan struct{})
	go func() {
		adapter.AdaptTick("RELIANCE", "INE001", 101.0, 200, time.Now())
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
