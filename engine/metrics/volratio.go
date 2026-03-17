package metrics

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"
	"time"

	"github.com/karanshergill/algotrix-go/utils"
)

// VolRatioResult holds the volume ratio analysis for a single ISIN.
type VolRatioResult struct {
	ISIN          string
	LatestVolume  float64 // volume on latest day
	AvgVolume20D  float64 // 20-day average volume
	VolumeRatio   float64 // latest / avg20d
	VolRatio5DAvg float64 // avg of last 5 days' volume / avg20d (smoothed surge)
	HighVolDays   int     // days in lookback where volume > 1.5x avg
	TradingDays   int
}

// ComputeVolRatio computes each stock's volume ratio against its own
// 20-day average volume over the most recent trading days.
//   - days: how many recent trading days to look back (must be >= 21)
//   - minCoverage: minimum fraction of days a stock must have data (0.0 to 1.0)
func ComputeVolRatio(db *sql.DB, days int, minCoverage float64) ([]VolRatioResult, error) {
	if days < 21 {
		return nil, fmt.Errorf("days must be at least 21 for volume ratio, got %d", days)
	}
	if minCoverage <= 0 || minCoverage > 1.0 {
		minCoverage = 1.0
	}

	dates, err := utils.TradingDates(db, days)
	if err != nil {
		return nil, fmt.Errorf("fetching trading dates: %w", err)
	}
	if len(dates) < 21 {
		return nil, nil
	}

	actualDays := len(dates)
	minDays := int(math.Ceil(float64(actualDays) * minCoverage))

	// dateIndex maps date string -> positional index for alignment.
	// TradingDates returns strings like "2026-03-16T00:00:00Z"; we also
	// index the short "2006-01-02" form so lookups work regardless of
	// how the driver formats the scanned date.
	dateIndex := make(map[string]int, actualDays*2)
	for i, d := range dates {
		dateIndex[d] = i
		if t, err := time.Parse(time.RFC3339, d); err == nil {
			dateIndex[t.Format("2006-01-02")] = i
		} else if t, err := time.Parse("2006-01-02", d); err == nil {
			dateIndex[t.Format(time.RFC3339)] = i
		}
	}

	// Fetch all volumes.
	rows, err := db.Query(
		`SELECT isin, date, volume
		   FROM nse_cm_bhavcopy
		  WHERE date = ANY($1)
		  ORDER BY isin, date ASC`, dates,
	)
	if err != nil {
		return nil, fmt.Errorf("querying bhavcopy: %w", err)
	}
	defer rows.Close()

	// volumes[isin][dateIdx] = volume (0 means missing).
	type volumeSeries struct {
		volumes []float64
	}
	allSeries := make(map[string]*volumeSeries)

	for rows.Next() {
		var isin string
		var dt time.Time
		var volume float64
		if err := rows.Scan(&isin, &dt, &volume); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}
		ds := dt.Format("2006-01-02")
		idx, ok := dateIndex[ds]
		if !ok {
			continue
		}
		s, exists := allSeries[isin]
		if !exists {
			s = &volumeSeries{volumes: make([]float64, actualDays)}
			allSeries[isin] = s
		}
		s.volumes[idx] = volume
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	// Compute volume ratio for each stock.
	var results []VolRatioResult

	for isin, s := range allSeries {
		// Collect non-zero volume days (most recent first).
		type dayVol struct {
			idx    int
			volume float64
		}
		var validDays []dayVol
		for i := actualDays - 1; i >= 0; i-- {
			if s.volumes[i] > 0 {
				validDays = append(validDays, dayVol{idx: i, volume: s.volumes[i]})
			}
		}

		if len(validDays) < minDays {
			continue
		}

		// 20-day average volume (use the 20 most recent days with data).
		limit20 := 20
		if len(validDays) < limit20 {
			limit20 = len(validDays)
		}
		var sum20 float64
		for k := 0; k < limit20; k++ {
			sum20 += validDays[k].volume
		}
		avgVolume20D := sum20 / float64(limit20)

		if avgVolume20D <= 0 {
			continue
		}

		latestVolume := validDays[0].volume
		volumeRatio := latestVolume / avgVolume20D

		// VolRatio5DAvg: average volume of last 5 days / avg20d.
		limit5 := 5
		if len(validDays) < limit5 {
			limit5 = len(validDays)
		}
		var sum5 float64
		for k := 0; k < limit5; k++ {
			sum5 += validDays[k].volume
		}
		volRatio5DAvg := (sum5 / float64(limit5)) / avgVolume20D

		// HighVolDays: count of days where volume > 1.5 * avg20d.
		var highVolDays int
		for _, dv := range validDays {
			if dv.volume > 1.5*avgVolume20D {
				highVolDays++
			}
		}

		results = append(results, VolRatioResult{
			ISIN:          isin,
			LatestVolume:  latestVolume,
			AvgVolume20D:  avgVolume20D,
			VolumeRatio:   volumeRatio,
			VolRatio5DAvg: volRatio5DAvg,
			HighVolDays:   highVolDays,
			TradingDays:   len(validDays),
		})
	}

	// Sort by VolumeRatio descending (biggest surges first).
	sort.Slice(results, func(i, j int) bool {
		return results[i].VolumeRatio > results[j].VolumeRatio
	})

	log.Printf("Computed VolRatio for %d ISINs over %d trading days (coverage %.0f%%)", len(results), actualDays, minCoverage*100)
	return results, nil
}
