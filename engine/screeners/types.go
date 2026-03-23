package screeners

import "time"

// SignalType represents the kind of signal.
type SignalType string

const (
	SignalAlert    SignalType = "ALERT"
	SignalBuy      SignalType = "BUY"
	SignalBreakout SignalType = "BREAKOUT"
)

// Signal represents a screener output.
type Signal struct {
	ScreenerName   string
	ISIN           string
	Symbol         string
	SignalType     SignalType
	LTP            float64
	TriggerPrice   float64   // price at signal time
	ThresholdPrice float64   // reference (prev close, N-session high, etc.)
	PercentAbove   float64   // % above threshold
	TriggeredAt    time.Time
	Metadata       map[string]interface{}
}

// Screener interface — each screener implements this.
type Screener interface {
	Name() string
	Evaluate(ctx *TickContext) *Signal
	Reset() // call on day rollover
}

// TickContext holds everything a screener needs to evaluate one stock.
type TickContext struct {
	ISIN     string
	Symbol   string
	LTP      float64
	Features map[string]float64 // from StockSnapshot.Features
	Market   MarketContext
	TickTime time.Time
	PrevLTP  float64 // previous LTP for this screener+ISIN (0 = first tick)
}

// MarketContext holds market-level data.
type MarketContext struct {
	NiftyLTP       float64
	NiftyPrevClose float64
}
