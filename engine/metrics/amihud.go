package metrics

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"

	"github.com/karanshergill/algotrix-go/utils"
)

// AmihudResult holds Amihud illiquidity metrics for a single ISIN.
type AmihudResult struct {
	ISIN         string
	Amihud       float64 // mean of daily |return| / traded_value
	MedianAmihud float64 // median of daily |return| / traded_value
	TradingDays  int     // how many days of data were used
}

// ComputeAmihud computes the Amihud Illiquidity Ratio for all ISINs.
//   - days: how many recent trading days to look back
//   - minCoverage: minimum fraction of days a stock must have data for (0.0 to 1.0, default 1.0 = 100%)
//
// Formula: avg( |daily_return| / traded_value ) where daily_return = (close - prev_close) / prev_close
// Lower values = more liquid (price moves less per rupee traded).
// Sorted ascending (most liquid first).
func ComputeAmihud(db *sql.DB, days int, minCoverage float64) ([]AmihudResult, error) {
	if days <= 0 {
		return nil, fmt.Errorf("days must be positive, got %d", days)
	}
	if minCoverage <= 0 || minCoverage > 1.0 {
		minCoverage = 1.0
	}

	// Get the last N distinct trading dates.
	dates, err := utils.TradingDates(db, days)
	if err != nil {
		return nil, fmt.Errorf("fetching trading dates: %w", err)
	}
	if len(dates) == 0 {
		return nil, nil
	}

	actualDays := len(dates)
	minDays := int(math.Ceil(float64(actualDays) * minCoverage))

	// Fetch rows for the exact dates in the lookback window.
	rows, err := db.Query(
		`SELECT isin, close, prev_close, traded_value
		   FROM nse_cm_bhavcopy
		  WHERE date = ANY($1)
		  ORDER BY isin, date ASC`,
		dates,
	)
	if err != nil {
		return nil, fmt.Errorf("querying bhavcopy: %w", err)
	}
	defer rows.Close()

	// Group daily Amihud ratios per ISIN.
	grouped := make(map[string][]float64)
	var order []string
	seen := make(map[string]bool)

	for rows.Next() {
		var isin string
		var cl, prevClose, tradedValue float64
		if err := rows.Scan(&isin, &cl, &prevClose, &tradedValue); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}

		if !seen[isin] {
			seen[isin] = true
			order = append(order, isin)
		}

		// Skip days where we can't compute a valid ratio.
		if prevClose == 0 || tradedValue == 0 {
			continue
		}

		dailyReturn := math.Abs((cl - prevClose) / prevClose)
		ratio := dailyReturn / tradedValue
		grouped[isin] = append(grouped[isin], ratio)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	var results []AmihudResult

	for _, isin := range order {
		ratios := grouped[isin]
		n := len(ratios)
		if n < minDays {
			continue
		}

		// Mean Amihud.
		var sum float64
		for _, r := range ratios {
			sum += r
		}
		meanAmihud := sum / float64(n)

		// Median Amihud.
		sorted := make([]float64, n)
		copy(sorted, ratios)
		sort.Float64s(sorted)
		var medianAmihud float64
		if n%2 == 1 {
			medianAmihud = sorted[n/2]
		} else {
			medianAmihud = (sorted[n/2-1] + sorted[n/2]) / 2
		}

		results = append(results, AmihudResult{
			ISIN:         isin,
			Amihud:       meanAmihud,
			MedianAmihud: medianAmihud,
			TradingDays:  n,
		})
	}

	// Sort ascending — most liquid (lowest Amihud) first.
	sort.Slice(results, func(i, j int) bool {
		return results[i].Amihud < results[j].Amihud
	})

	log.Printf("Computed Amihud for %d ISINs (lookback=%d, coverage=%.0f%%)", len(results), days, minCoverage*100)
	return results, nil
}
