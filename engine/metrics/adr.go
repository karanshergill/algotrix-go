package metrics

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"

	"github.com/karanshergill/algotrix-go/utils"
)

// ADRResult holds Average Daily Range metrics for a single ISIN.
type ADRResult struct {
	ISIN        string
	ADR         float64 // average daily range in rupees (high - low)
	ADRPct      float64 // average daily range as percentage of open
	MedianDR    float64 // median daily range in rupees
	MedianDRPct float64 // median daily range as percentage of open
	TradingDays int     // how many days of data were used
}

// ComputeADR computes Average Daily Range for all ISINs.
//   - days: how many recent trading days to look back
//   - minCoverage: minimum fraction of days a stock must have data for (0.0 to 1.0)
func ComputeADR(db *sql.DB, days int, minCoverage float64) ([]ADRResult, error) {
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
		`SELECT isin, open, high, low
		   FROM nse_cm_bhavcopy
		  WHERE date = ANY($1)
		  ORDER BY isin, date`, dates,
	)
	if err != nil {
		return nil, fmt.Errorf("querying bhavcopy: %w", err)
	}
	defer rows.Close()

	type dayRange struct {
		rangeAbs float64 // high - low
		rangePct float64 // (high - low) / open * 100
	}
	grouped := make(map[string][]dayRange)

	for rows.Next() {
		var isin string
		var open, high, low float64
		if err := rows.Scan(&isin, &open, &high, &low); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}

		// Skip rows where open is zero (prevents division by zero).
		if open <= 0 {
			continue
		}

		grouped[isin] = append(grouped[isin], dayRange{
			rangeAbs: high - low,
			rangePct: (high - low) / open * 100,
		})
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	var results []ADRResult

	for isin, ranges := range grouped {
		n := len(ranges)
		if n < minDays {
			continue
		}

		absVals := make([]float64, n)
		pctVals := make([]float64, n)
		for i, r := range ranges {
			absVals[i] = r.rangeAbs
			pctVals[i] = r.rangePct
		}

		results = append(results, ADRResult{
			ISIN:        isin,
			ADR:         mean(absVals),
			ADRPct:      mean(pctVals),
			MedianDR:    median(absVals),
			MedianDRPct: median(pctVals),
			TradingDays: n,
		})
	}

	sort.Slice(results, func(i, j int) bool {
		return results[i].ADRPct > results[j].ADRPct
	})

	log.Printf("Computed ADR for %d ISINs over %d trading days (coverage %.0f%%)", len(results), actualDays, minCoverage*100)
	return results, nil
}
