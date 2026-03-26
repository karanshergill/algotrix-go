package screeners

import (
	"context"
	"log"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/karanshergill/algotrix-go/utils"
)

// Setup creates and returns a fully initialized screener engine.
// Uses the atdb pool directly — no separate algotrix connection needed.
func Setup(ctx context.Context, pool *pgxpool.Pool) (*Engine, error) {
	// 1. Create signal DB using atdb pool
	db := NewSignalDB(pool)

	// 2. Load breakout thresholds for today
	today := time.Now().In(utils.IST)

	thresholds, err := LoadBreakoutThresholds(ctx, pool, today)
	if err != nil {
		log.Printf("[screener-setup] WARNING: breakout thresholds failed: %v (breakout screener will be dormant)", err)
		thresholds = make(map[string]float64)
	}

	// 3. Create all 5 screeners
	screenerList := []Screener{
		NewEarlyMomentumScreener(),
		NewSniperScreener(),
		NewTridentScreener(),
		// NewThinMomentumScreener(),    // disabled per Ricky
		// NewBreakoutScreener(thresholds), // disabled per Ricky
	}

	// 4. Create engine
	engine := NewEngine(screenerList, db)

	log.Printf("[screener-setup] screener engine ready — %d screeners, %d breakout thresholds",
		len(screenerList), len(thresholds))

	return engine, nil
}
