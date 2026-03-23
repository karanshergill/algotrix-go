package features

import (
	"fmt"
	"log"
	"math"
	"time"
)

// GuardConfig holds thresholds for tick validation.
type GuardConfig struct {
	MaxPriceJumpPct  float64 // reject if |LTP change| > X% in one tick (default 20%)
	MaxSpreadBps     float64 // reject if spread > X bps (default 500)
	MinLTP           float64 // reject if LTP below this (default 0.01)
	AllowVolumeReset bool    // handle reconnect volume resets gracefully
}

// DefaultGuardConfig returns sensible defaults for NSE equities.
func DefaultGuardConfig() *GuardConfig {
	return &GuardConfig{
		MaxPriceJumpPct:  20.0,
		MaxSpreadBps:     500.0,
		MinLTP:           0.01,
		AllowVolumeReset: true,
	}
}

// FeedGuard validates incoming ticks for sanity before they enter the engine.
type FeedGuard struct {
	lastTS     time.Time
	lastVolume int64
	lastLTP    float64
	config     *GuardConfig
}

// NewFeedGuard creates a FeedGuard with the given config.
func NewFeedGuard(config *GuardConfig) *FeedGuard {
	return &FeedGuard{config: config}
}

// ValidateTick returns true if the tick is sane, false with a reason to reject.
func (g *FeedGuard) ValidateTick(isin string, ltp float64, volume int64, ts time.Time) (bool, string) {
	// Reject zero/negative LTP
	if ltp <= 0 {
		return false, "ltp <= 0"
	}

	// Reject backward timestamps (non-monotonic)
	if !g.lastTS.IsZero() && ts.Before(g.lastTS) {
		return false, "timestamp went backward"
	}

	// Reject insane price jumps (circuit breaker: NSE max is 20%)
	if g.lastLTP > 0 {
		jumpPct := math.Abs(ltp-g.lastLTP) / g.lastLTP * 100
		if jumpPct > g.config.MaxPriceJumpPct {
			return false, fmt.Sprintf("price jump %.1f%% exceeds max %.1f%%", jumpPct, g.config.MaxPriceJumpPct)
		}
	}

	// Handle volume reset (reconnect scenario)
	if volume < g.lastVolume && g.config.AllowVolumeReset {
		log.Printf("[FeedGuard] %s volume reset: %d → %d (reconnect?)", isin, g.lastVolume, volume)
	}

	g.lastTS = ts
	g.lastVolume = volume
	g.lastLTP = ltp
	return true, ""
}
