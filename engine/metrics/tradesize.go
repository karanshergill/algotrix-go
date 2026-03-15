package metrics

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"

	"github.com/karanshergill/algotrix-go/utils"
)

// TradeSizeResult holds average trade size metrics for a single ISIN.
type TradeSizeResult struct {
	ISIN            string
	AvgTradeSize    float64 // mean of daily (traded_value / num_trades) in ₹
	MedianTradeSize float64 // median of daily (traded_value / num_trades) in ₹
	TradingDays     int     // how many valid days of data were used
}

// ComputeTradeSize computes the average trade size for all ISINs.
//   - days: how many recent trading days to look back
//   - minCoverage: minimum fraction of days a stock must have data for (0.0 to 1.0, default 1.0 = 100%)
//
// Formula: traded_value / num_trades per day, then averaged over the lookback window.
// Larger values indicate institutional participation (bigger orders).
// Smaller values indicate retail-dominated trading (many small orders).
// Sorted descending (largest avg trade size first).
func ComputeTradeSize(db *sql.DB, days int, minCoverage float64) ([]TradeSizeResult, error) {
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
		`SELECT isin, traded_value, num_trades
		   FROM nse_cm_bhavcopy
		  WHERE date = ANY($1)
		  ORDER BY isin, date ASC`,
		dates,
	)
	if err != nil {
		return nil, fmt.Errorf("querying bhavcopy: %w", err)
	}
	defer rows.Close()

	// Group daily trade sizes per ISIN.
	grouped := make(map[string][]float64)
	var order []string
	seen := make(map[string]bool)

	for rows.Next() {
		var isin string
		var tradedValue float64
		var numTrades int64
		if err := rows.Scan(&isin, &tradedValue, &numTrades); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}

		if !seen[isin] {
			seen[isin] = true
			order = append(order, isin)
		}

		// Skip days with no trades (can't divide by zero).
		if numTrades == 0 {
			continue
		}

		dailySize := tradedValue / float64(numTrades)
		grouped[isin] = append(grouped[isin], dailySize)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	var results []TradeSizeResult

	for _, isin := range order {
		sizes := grouped[isin]
		n := len(sizes)
		if n < minDays {
			continue
		}

		// Mean trade size.
		var sum float64
		for _, s := range sizes {
			sum += s
		}
		avgSize := sum / float64(n)

		// Median trade size.
		sorted := make([]float64, n)
		copy(sorted, sizes)
		sort.Float64s(sorted)
		var medianSize float64
		if n%2 == 1 {
			medianSize = sorted[n/2]
		} else {
			medianSize = (sorted[n/2-1] + sorted[n/2]) / 2
		}

		results = append(results, TradeSizeResult{
			ISIN:            isin,
			AvgTradeSize:    avgSize,
			MedianTradeSize: medianSize,
			TradingDays:     n,
		})
	}

	// Sort descending — largest avg trade size first.
	sort.Slice(results, func(i, j int) bool {
		return results[i].AvgTradeSize > results[j].AvgTradeSize
	})

	log.Printf("Computed TradeSize for %d ISINs (lookback=%d, coverage=%.0f%%)", len(results), days, minCoverage*100)
	return results, nil
}
