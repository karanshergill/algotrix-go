package screeners

import (
	"context"
	"fmt"
	"log"
	"time"
)

// Setup creates and returns a fully initialized screener engine.
// algotrixDSN connects to the algotrix DB (not atdb).
func Setup(ctx context.Context, algotrixDSN string) (*Engine, error) {
	// 1. Connect to algotrix DB for signal persistence
	db, err := NewSignalDB(ctx, algotrixDSN)
	if err != nil {
		return nil, fmt.Errorf("signal DB setup: %w", err)
	}

	// 2. Load breakout thresholds for today
	ist := time.FixedZone("IST", 5*3600+30*60)
	today := time.Now().In(ist)

	// Use today's date for session extremes lookup
	thresholds, err := LoadBreakoutThresholds(ctx, db.pool, today)
	if err != nil {
		log.Printf("[screener-setup] WARNING: breakout thresholds failed: %v (breakout screener will be dormant)", err)
		thresholds = make(map[string]float64)
	}

	// 3. Create all 5 screeners
	screenerList := []Screener{
		NewEarlyMomentumScreener(),
		NewSniperScreener(),
		NewTridentScreener(),
		NewThinMomentumScreener(),
		NewBreakoutScreener(thresholds),
	}

	// 4. Create engine
	engine := NewEngine(screenerList, db)

	log.Printf("[screener-setup] screener engine ready — %d screeners, %d breakout thresholds",
		len(screenerList), len(thresholds))

	return engine, nil
}
