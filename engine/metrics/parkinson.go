package metrics

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"

	"github.com/karanshergill/algotrix-go/utils"
)

// ParkinsonResult holds Parkinson volatility metrics for a single ISIN.
type ParkinsonResult struct {
	ISIN           string
	Parkinson      float64 // annualized Parkinson volatility (daily * sqrt(252))
	ParkinsonDaily float64 // daily Parkinson volatility (raw)
	TradingDays    int     // how many valid days of data were used
}

// ComputeParkinson computes the Parkinson volatility estimator for all ISINs.
//   - days: how many recent trading days to look back
//   - minCoverage: minimum fraction of days a stock must have data for (0.0 to 1.0, default 1.0 = 100%)
//
// Formula: σ_daily = sqrt( (1 / (N * 4 * ln(2))) * Σ ln(H/L)² )
// Annualized: σ_annual = σ_daily * sqrt(252)
// Uses only high and low prices — captures intraday range, ignores overnight gaps.
// Higher values = more intraday movement.
// Sorted descending (most volatile first).
func ComputeParkinson(db *sql.DB, days int, minCoverage float64) ([]ParkinsonResult, error) {
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
		`SELECT isin, high, low
		   FROM nse_cm_bhavcopy
		  WHERE date = ANY($1)
		  ORDER BY isin, date ASC`,
		dates,
	)
	if err != nil {
		return nil, fmt.Errorf("querying bhavcopy: %w", err)
	}
	defer rows.Close()

	// Group valid ln(H/L)² values per ISIN.
	grouped := make(map[string][]float64)
	var order []string
	seen := make(map[string]bool)

	for rows.Next() {
		var isin string
		var high, low float64
		if err := rows.Scan(&isin, &high, &low); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}

		if !seen[isin] {
			seen[isin] = true
			order = append(order, isin)
		}

		// Skip days with bad data.
		if high <= 0 || low <= 0 || high < low {
			continue
		}

		// ln(H/L)² — if high == low, ln(1) = 0, contributes zero (valid).
		lnHL := math.Log(high / low)
		grouped[isin] = append(grouped[isin], lnHL*lnHL)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	// Constant: 1 / (4 * ln(2))
	factor := 1.0 / (4.0 * math.Ln2)

	var results []ParkinsonResult

	for _, isin := range order {
		sqLnHLs := grouped[isin]
		n := len(sqLnHLs)
		if n < minDays {
			continue
		}

		// Parkinson variance = factor * (1/N) * Σ ln(H/L)²
		var sumSqLnHL float64
		for _, v := range sqLnHLs {
			sumSqLnHL += v
		}
		variance := factor * sumSqLnHL / float64(n)
		daily := math.Sqrt(variance)

		// Annualized: daily * sqrt(252 NSE trading days/year)
		annualized := daily * math.Sqrt(252)

		results = append(results, ParkinsonResult{
			ISIN:           isin,
			Parkinson:      annualized,
			ParkinsonDaily: daily,
			TradingDays:    n,
		})
	}

	// Sort descending — most volatile (highest Parkinson) first.
	sort.Slice(results, func(i, j int) bool {
		return results[i].Parkinson > results[j].Parkinson
	})

	log.Printf("Computed Parkinson for %d ISINs (lookback=%d, coverage=%.0f%%)", len(results), days, minCoverage*100)
	return results, nil
}
