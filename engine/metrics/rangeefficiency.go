package metrics

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"

	"github.com/karanshergill/algotrix-go/utils"
)

// RangeEfficiencyResult holds Range Efficiency metrics for a single ISIN.
// Range Efficiency = |close - open| / (high - low)
// 0.0 = pure doji (choppy, no net movement), 1.0 = perfect trend (all range is capturable).
type RangeEfficiencyResult struct {
	ISIN               string
	AvgRangeEfficiency float64 // mean of daily range efficiency
	MedianRangeEff     float64 // median of daily range efficiency
	TradingDays        int     // how many days of data were used
}

// ComputeRangeEfficiency computes the average and median Range Efficiency for all ISINs.
//   - days: how many recent trading days to look back
//   - minCoverage: minimum fraction of days a stock must have data for (0.0 to 1.0)
func ComputeRangeEfficiency(db *sql.DB, days int, minCoverage float64) ([]RangeEfficiencyResult, error) {
	if days <= 0 {
		return nil, fmt.Errorf("days must be positive, got %d", days)
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
		`SELECT isin, open, high, low, close
		   FROM nse_cm_bhavcopy
		  WHERE date = ANY($1)
		  ORDER BY isin, date`, dates,
	)
	if err != nil {
		return nil, fmt.Errorf("querying bhavcopy: %w", err)
	}
	defer rows.Close()

	grouped := make(map[string][]float64) // ISIN -> daily efficiency values

	for rows.Next() {
		var isin string
		var open, high, low, close float64
		if err := rows.Scan(&isin, &open, &high, &low, &close); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}

		dayRange := high - low
		// Skip days with zero range (no price movement at all).
		if dayRange <= 0 {
			continue
		}

		efficiency := math.Abs(close-open) / dayRange
		grouped[isin] = append(grouped[isin], efficiency)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	var results []RangeEfficiencyResult

	for isin, efficiencies := range grouped {
		n := len(efficiencies)
		if n < minDays {
			continue
		}

		results = append(results, RangeEfficiencyResult{
			ISIN:               isin,
			AvgRangeEfficiency: mean(efficiencies),
			MedianRangeEff:     median(efficiencies),
			TradingDays:        n,
		})
	}

	sort.Slice(results, func(i, j int) bool {
		return results[i].AvgRangeEfficiency > results[j].AvgRangeEfficiency
	})

	log.Printf("Computed Range Efficiency for %d ISINs over %d trading days (coverage %.0f%%)", len(results), actualDays, minCoverage*100)
	return results, nil
}
