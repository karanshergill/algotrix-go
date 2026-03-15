package metrics

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"

	"github.com/karanshergill/algotrix-go/utils"
)

// ADTVResult holds ADTV metrics for a single ISIN.
type ADTVResult struct {
	ISIN        string
	ADTV        float64 // simple average of traded_value
	MADTV       float64 // median of traded_value
	AvgVolume   float64 // simple average of volume
	MedianVol   float64 // median of volume
	TradingDays int     // how many days of data were used
}

// ComputeADTV computes ADTV and MADTV for all ISINs.
//   - days: how many recent trading days to look back
//   - minCoverage: minimum fraction of days a stock must have data for (0.0 to 1.0, default 1.0 = 100%)
func ComputeADTV(db *sql.DB, days int, minCoverage float64) ([]ADTVResult, error) {
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

	// Query all rows for those dates.
	rows, err := db.Query(
		`SELECT isin, traded_value, volume
		   FROM nse_cm_bhavcopy
		  WHERE date = ANY($1)
		  ORDER BY isin, date`, dates,
	)
	if err != nil {
		return nil, fmt.Errorf("querying bhavcopy: %w", err)
	}
	defer rows.Close()

	type isinData struct {
		tradedValues []float64
		volumes      []float64
	}
	grouped := make(map[string]*isinData)

	for rows.Next() {
		var isin string
		var tradedValue float64
		var volume int64
		if err := rows.Scan(&isin, &tradedValue, &volume); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}
		d, ok := grouped[isin]
		if !ok {
			d = &isinData{}
			grouped[isin] = d
		}
		d.tradedValues = append(d.tradedValues, tradedValue)
		d.volumes = append(d.volumes, float64(volume))
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	var results []ADTVResult

	for isin, d := range grouped {
		n := len(d.tradedValues)
		if n < minDays {
			continue
		}

		results = append(results, ADTVResult{
			ISIN:        isin,
			ADTV:        mean(d.tradedValues),
			MADTV:       median(d.tradedValues),
			AvgVolume:   mean(d.volumes),
			MedianVol:   median(d.volumes),
			TradingDays: n,
		})
	}

	sort.Slice(results, func(i, j int) bool {
		return results[i].MADTV > results[j].MADTV
	})

	log.Printf("Computed ADTV for %d ISINs over %d trading days (coverage %.0f%%)", len(results), actualDays, minCoverage*100)
	return results, nil
}

func mean(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	var sum float64
	for _, v := range vals {
		sum += v
	}
	return sum / float64(len(vals))
}

func median(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	sorted := make([]float64, len(vals))
	copy(sorted, vals)
	sort.Float64s(sorted)

	n := len(sorted)
	if n%2 == 1 {
		return sorted[n/2]
	}
	return (sorted[n/2-1] + sorted[n/2]) / 2
}
