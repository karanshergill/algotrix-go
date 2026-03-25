package features

import (
	"testing"
	"time"
)

func TestClassifyTick_QuoteRule(t *testing.T) {
	now := time.Date(2026, 1, 1, 9, 15, 0, 0, time.UTC)

	tests := []struct {
		name        string
		bid         float64
		ask         float64
		price       float64
		lastLTP     float64
		lastDir     int8
		totalBidQty int64
		totalAskQty int64
		wantBuy     int64
		wantSell    int64
	}{
		{
			name:    "no depth, first unchanged tick → skip",
			bid:     0, ask: 0,
			price:   100, lastLTP: 100, lastDir: 0,
			wantBuy: 0, wantSell: 0,
		},
		{
			name:    "no depth, uptick → BUY",
			bid:     0, ask: 0,
			price:   101, lastLTP: 100,
			wantBuy: 500, wantSell: 0,
		},
		{
			name:     "no depth, downtick → SELL",
			bid:      0, ask: 0,
			price:    99, lastLTP: 100,
			wantBuy:  0, wantSell: 500,
		},
		{
			name:    "depth, price >= ask → BUY",
			bid:     99.5, ask: 100.5,
			price:   100.5,
			wantBuy: 500, wantSell: 0,
		},
		{
			name:     "depth, price <= bid → SELL",
			bid:      99.5, ask: 100.5,
			price:    99.5,
			wantBuy:  0, wantSell: 500,
		},
		{
			name:    "depth, price > mid → BUY",
			bid:     99, ask: 101,
			price:   100.5,
			wantBuy: 500, wantSell: 0,
		},
		{
			name:     "depth, price < mid → SELL",
			bid:      99, ask: 101,
			price:    99.5,
			wantBuy:  0, wantSell: 500,
		},
		{
			name:        "depth, price == mid, bidQty > askQty → BUY",
			bid:         99, ask: 101,
			price:       100,
			totalBidQty: 1000, totalAskQty: 500,
			wantBuy:     500, wantSell: 0,
		},
		{
			name:        "depth, price == mid, bidQty <= askQty → SELL",
			bid:         99, ask: 101,
			price:       100,
			totalBidQty: 500, totalAskQty: 1000,
			wantBuy:     0, wantSell: 500,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s := &StockState{
				LastLTP:       tt.lastLTP,
				LastDirection: tt.lastDir,
				TotalBidQty:  tt.totalBidQty,
				TotalAskQty:  tt.totalAskQty,
				BuyVol5m:     NewRollingSum(300*time.Second, 1024),
				SellVol5m:    NewRollingSum(300*time.Second, 1024),
			}
			s.BidPrices[0] = tt.bid
			s.AskPrices[0] = tt.ask

			s.ClassifyTick(tt.price, 500, now)

			if s.CumulativeBuyVol != tt.wantBuy {
				t.Errorf("CumulativeBuyVol = %d, want %d", s.CumulativeBuyVol, tt.wantBuy)
			}
			if s.CumulativeSellVol != tt.wantSell {
				t.Errorf("CumulativeSellVol = %d, want %d", s.CumulativeSellVol, tt.wantSell)
			}
		})
	}
}
