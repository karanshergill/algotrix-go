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

// GapResult holds the gap% statistics of a single ISIN.
type GapResult struct {
	ISIN         string
	LatestGapPct float64 // gap% on the most recent day
	AvgAbsGapPct float64 // average |gap%| over lookback (gap volatility)
	AvgGapPct    float64 // average gap% with sign (directional bias)
	GapUpDays    int     // number of days with positive gap
	GapDownDays  int     // number of days with negative gap
	TradingDays  int     // total days of data
}

// ComputeGap computes overnight gap statistics for each stock over
// the most recent trading days.
//   - days: how many recent trading days to look back
//   - minCoverage: minimum fraction of days a stock must have data for (0.0 to 1.0)
func ComputeGap(db *sql.DB, days int, minCoverage float64) ([]GapResult, error) {
	if days < 1 {
		return nil, fmt.Errorf("days must be at least 1 for gap, got %d", days)
	}
	if minCoverage <= 0 || minCoverage > 1.0 {
		minCoverage = 1.0
	}

	dates, err := utils.TradingDates(db, days)
	if err != nil {
		return nil, fmt.Errorf("fetching trading dates: %w", err)
	}
	if len(dates) < 1 {
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

	// --- Fetch open and prev_close ---
	rows, err := db.Query(
		`SELECT isin, date, open, prev_close
		   FROM nse_cm_bhavcopy
		  WHERE date = ANY($1)
		  ORDER BY isin, date ASC`, dates,
	)
	if err != nil {
		return nil, fmt.Errorf("querying bhavcopy: %w", err)
	}
	defer rows.Close()

	type dayData struct {
		open      float64
		prevClose float64
	}
	// series[isin][dateIdx] = dayData
	type gapSeries struct {
		data []dayData
	}
	allSeries := make(map[string]*gapSeries)

	for rows.Next() {
		var isin string
		var dt time.Time
		var openPrice, prevClose float64
		if err := rows.Scan(&isin, &dt, &openPrice, &prevClose); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}
		ds := dt.Format("2006-01-02")
		idx, ok := dateIndex[ds]
		if !ok {
			continue
		}
		s, exists := allSeries[isin]
		if !exists {
			s = &gapSeries{data: make([]dayData, actualDays)}
			allSeries[isin] = s
		}
		s.data[idx] = dayData{open: openPrice, prevClose: prevClose}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	// --- Compute gap% for each stock ---
	var results []GapResult

	for isin, s := range allSeries {
		var gaps []float64
		var latestIdx int = -1

		for i := 0; i < actualDays; i++ {
			d := s.data[i]
			if d.open <= 0 || d.prevClose <= 0 {
				continue
			}
			gapPct := (d.open - d.prevClose) / d.prevClose * 100
			gaps = append(gaps, gapPct)
			latestIdx = len(gaps) - 1
		}

		if len(gaps) < minDays {
			continue
		}

		var sumAbs, sumSigned float64
		var upDays, downDays int
		for _, g := range gaps {
			sumAbs += math.Abs(g)
			sumSigned += g
			if g > 0 {
				upDays++
			} else if g < 0 {
				downDays++
			}
		}
		n := float64(len(gaps))

		results = append(results, GapResult{
			ISIN:         isin,
			LatestGapPct: gaps[latestIdx],
			AvgAbsGapPct: sumAbs / n,
			AvgGapPct:    sumSigned / n,
			GapUpDays:    upDays,
			GapDownDays:  downDays,
			TradingDays:  len(gaps),
		})
	}

	// Sort by AvgAbsGapPct descending (biggest gappers first).
	sort.Slice(results, func(i, j int) bool {
		return results[i].AvgAbsGapPct > results[j].AvgAbsGapPct
	})

	log.Printf("Computed Gap for %d ISINs over %d trading days (coverage %.0f%%)", len(results), actualDays, minCoverage*100)
	return results, nil
}
