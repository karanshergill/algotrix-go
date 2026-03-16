package metrics

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"

	"github.com/karanshergill/algotrix-go/utils"
)

// MomentumResult holds short-term momentum metrics for a single ISIN.
type MomentumResult struct {
	ISIN            string
	Return5D        float64 // 5-day return: (close_latest / close_5d_ago) - 1
	Return10D       float64 // 10-day return: (close_latest / close_10d_ago) - 1
	ConsecUpDays    int     // consecutive up-close days (ending at latest)
	ConsecDownDays  int     // consecutive down-close days (ending at latest)
	TradingDays     int     // total days of data available
}

// ComputeMomentum computes short-term momentum for all ISINs.
// Uses the full lookback period to calculate returns and streaks.
//   - days: how many recent trading days to look back (must be >= 10 for 10D return)
//   - minCoverage: minimum fraction of days a stock must have data for (0.0 to 1.0)
func ComputeMomentum(db *sql.DB, days int, minCoverage float64) ([]MomentumResult, error) {
	if days < 10 {
		return nil, fmt.Errorf("days must be at least 10 for momentum, got %d", days)
	}
	if minCoverage <= 0 || minCoverage > 1.0 {
		minCoverage = 1.0
	}

	dates, err := utils.TradingDates(db, days)
	if err != nil {
		return nil, fmt.Errorf("fetching trading dates: %w", err)
	}
	if len(dates) == 0 {
		return nil, nil
	}

	actualDays := len(dates)
	minDays := int(math.Ceil(float64(actualDays) * minCoverage))

	rows, err := db.Query(
		`SELECT isin, date, close
		   FROM nse_cm_bhavcopy
		  WHERE date = ANY($1)
		  ORDER BY isin, date ASC`, dates,
	)
	if err != nil {
		return nil, fmt.Errorf("querying bhavcopy: %w", err)
	}
	defer rows.Close()

	type dailyClose struct {
		close float64
	}
	// Ordered slice of closes per ISIN (oldest first).
	grouped := make(map[string][]dailyClose)

	for rows.Next() {
		var isin string
		var date interface{} // we don't need the actual date value
		var closePrice float64
		if err := rows.Scan(&isin, &date, &closePrice); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}
		grouped[isin] = append(grouped[isin], dailyClose{close: closePrice})
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	var results []MomentumResult

	for isin, closes := range grouped {
		n := len(closes)
		if n < minDays {
			continue
		}

		latest := closes[n-1].close
		if latest <= 0 {
			continue
		}

		// 5-day return.
		var ret5d float64
		if n >= 6 { // need at least 6 points for 5-day return
			close5dAgo := closes[n-6].close
			if close5dAgo > 0 {
				ret5d = (latest / close5dAgo) - 1
			}
		}

		// 10-day return.
		var ret10d float64
		if n >= 11 { // need at least 11 points for 10-day return
			close10dAgo := closes[n-11].close
			if close10dAgo > 0 {
				ret10d = (latest / close10dAgo) - 1
			}
		}

		// Consecutive up/down days (from latest backwards).
		consecUp := 0
		consecDown := 0
		for i := n - 1; i >= 1; i-- {
			change := closes[i].close - closes[i-1].close
			if i == n-1 {
				// First comparison sets direction.
				if change > 0 {
					consecUp = 1
				} else if change < 0 {
					consecDown = 1
				}
				continue
			}
			if change > 0 && consecUp > 0 && consecDown == 0 {
				consecUp++
			} else if change < 0 && consecDown > 0 && consecUp == 0 {
				consecDown++
			} else {
				break
			}
		}

		results = append(results, MomentumResult{
			ISIN:           isin,
			Return5D:       ret5d,
			Return10D:      ret10d,
			ConsecUpDays:   consecUp,
			ConsecDownDays: consecDown,
			TradingDays:    n,
		})
	}

	// Sort by absolute 5D return descending (strongest movers first).
	sort.Slice(results, func(i, j int) bool {
		return math.Abs(results[i].Return5D) > math.Abs(results[j].Return5D)
	})

	log.Printf("Computed Momentum for %d ISINs over %d trading days (coverage %.0f%%)", len(results), actualDays, minCoverage*100)
	return results, nil
}
