package screeners

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// LoadBreakoutThresholds loads 2-session high thresholds from daily_session_extremes.
// Returns ISIN → high_value mapping.
func LoadBreakoutThresholds(ctx context.Context, pool *pgxpool.Pool, sessionDate time.Time) (map[string]float64, error) {
	dateStr := sessionDate.Format("2006-01-02")

	rows, err := pool.Query(ctx,
		`SELECT sm.isin, dse.high_value
		 FROM daily_session_extremes dse
		 JOIN scrip_master sm ON sm.security_id = dse.security_id
		 WHERE dse.indicator = 'price'
		   AND dse.lookback_sessions = 2
		   AND dse.session_date = $1
		   AND dse.high_value IS NOT NULL
		   AND sm.isin IS NOT NULL AND sm.isin != ''`, dateStr)
	if err != nil {
		return nil, fmt.Errorf("query breakout thresholds: %w", err)
	}
	defer rows.Close()

	thresholds := make(map[string]float64)
	for rows.Next() {
		var isin string
		var highVal float64
		if err := rows.Scan(&isin, &highVal); err != nil {
			return nil, err
		}
		thresholds[isin] = highVal
	}
	log.Printf("[screener-loader] loaded %d breakout thresholds for %s", len(thresholds), dateStr)
	return thresholds, rows.Err()
}
