package features

import (
	"log"
	"time"

	"github.com/karanshergill/algotrix-go/feed"
)

// FeedAdapter bridges the existing feed layer to FeatureEngine.
// It converts feed tick/depth events into engine channel events.
type FeedAdapter struct {
	engine *FeatureEngine
}

// NewFeedAdapter creates a FeedAdapter and wires the hub broadcaster.
func NewFeedAdapter(engine *FeatureEngine, hub *feed.Hub) *FeedAdapter {
	if hub != nil {
		engine.SetHub(NewHubAdapter(hub.Broadcast))
	}
	return &FeedAdapter{engine: engine}
}

// AdaptTick converts a feed tick into a TickEvent and sends it to the engine.
// Non-blocking: drops the event if the channel is full.
func (fa *FeedAdapter) AdaptTick(data feed.TickData) {
	ev := TickEvent{
		ISIN:           data.ISIN,
		Symbol:         data.Symbol,
		LTP:            data.LTP,
		Volume:         data.Volume,
		TS:             data.TS,
		OpenPrice:      data.OpenPrice,
		HighPrice:      data.HighPrice,
		LowPrice:       data.LowPrice,
		PrevClosePrice: data.PrevClosePrice,
		Change:         data.Change,
		ChangePct:      data.ChangePct,
		TotBuyQty:      data.TotBuyQty,
		TotSellQty:     data.TotSellQty,
		BidPrice:       data.BidPrice,
		AskPrice:       data.AskPrice,
		BidSize:        data.BidSize,
		AskSize:        data.AskSize,
		AvgTradePrice:  data.AvgTradePrice,
		LastTradedQty:  data.LastTradedQty,
		LastTradedTime: data.LastTradedTime,
		ExchFeedTime:   data.ExchFeedTime,
		OI:             data.OI,
		YearHigh:       data.YearHigh,
		YearLow:        data.YearLow,
		LowerCircuit:   data.LowerCircuit,
		UpperCircuit:   data.UpperCircuit,
	}
	select {
	case fa.engine.tickCh <- ev:
	default:
		log.Printf("[FeedAdapter] WARN: tick channel full, dropping tick for %s (%s)", data.Symbol, data.ISIN)
	}
}

// AdaptDepth converts feed depth data into a DepthEvent and sends it to the engine.
// Non-blocking: drops the event if the channel is full.
func (fa *FeedAdapter) AdaptDepth(isin string, bids, asks []DepthLevel, ts time.Time) {
	ev := DepthEvent{
		ISIN: isin,
		Bids: bids,
		Asks: asks,
		TS:   ts,
	}
	select {
	case fa.engine.depthCh <- ev:
	default:
		log.Printf("[FeedAdapter] WARN: depth channel full, dropping depth for %s", isin)
	}
}
