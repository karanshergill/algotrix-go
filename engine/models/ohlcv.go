package models

import "time"

// OHLCV represents a single candle at any resolution.
type OHLCV struct {
	ISIN      string
	Timestamp time.Time
	Open      float64
	High      float64
	Low       float64
	Close     float64
	Volume    int64
}
