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

// EMASlopeResult holds the EMA slope analysis for a single ISIN.
type EMASlopeResult struct {
	ISIN       string
	Slope10D   float64 // daily slope of 10-period EMA (% per day)
	Slope20D   float64 // daily slope of 20-period EMA (% per day)
	Slope50D   float64 // daily slope of 50-period EMA (% per day)
	Accel10D   float64 // acceleration: slope change over last 5 days (slope now - slope 5d ago)
	TrendState string  // "strong_up" | "up" | "flat" | "down" | "strong_down"
	TrendScore float64 // composite score: weighted blend of slopes (-100 to +100 range, roughly)
}

// ComputeEMASlope computes the slope and acceleration of exponential moving
// averages at 10/20/50 periods for each stock.
//   - days: how many recent trading days to look back (must be >= 60 for 50-EMA warm-up)
//   - minCoverage: minimum fraction of days a stock must have data (0.0 to 1.0)
func ComputeEMASlope(db *sql.DB, days int, minCoverage float64) ([]EMASlopeResult, error) {
	if days < 60 {
		return nil, fmt.Errorf("days must be at least 60 for EMA slope (50-period warm-up), got %d", days)
	}
	if minCoverage <= 0 || minCoverage > 1.0 {
		minCoverage = 1.0
	}

	dates, err := utils.TradingDates(db, days)
	if err != nil {
		return nil, fmt.Errorf("fetching trading dates: %w", err)
	}
	if len(dates) < 60 {
		return nil, nil
	}

	actualDays := len(dates)
	minDays := int(math.Ceil(float64(actualDays) * minCoverage))

	// dateIndex maps date string -> positional index.
	// TradingDates returns newest-first; we reverse so index 0 = oldest.
	dateIndex := make(map[string]int, actualDays*2)
	for i, d := range dates {
		// Reverse: newest-first → oldest-first indexing
		revIdx := actualDays - 1 - i
		dateIndex[d] = revIdx
		if t, err := time.Parse(time.RFC3339, d); err == nil {
			dateIndex[t.Format("2006-01-02")] = revIdx
		} else if t, err := time.Parse("2006-01-02", d); err == nil {
			dateIndex[t.Format(time.RFC3339)] = revIdx
		}
	}

	// Fetch all closes.
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

	type closeSeries struct {
		closes []float64
	}
	allSeries := make(map[string]*closeSeries)

	for rows.Next() {
		var isin string
		var dt time.Time
		var closePrice float64
		if err := rows.Scan(&isin, &dt, &closePrice); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}
		ds := dt.Format("2006-01-02")
		idx, ok := dateIndex[ds]
		if !ok {
			continue
		}
		s, exists := allSeries[isin]
		if !exists {
			s = &closeSeries{closes: make([]float64, actualDays)}
			allSeries[isin] = s
		}
		s.closes[idx] = closePrice
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	// computeEMA computes an EMA series from closes (index 0 = oldest).
	// Returns the EMA values at each position; NaN where not enough warm-up data.
	computeEMA := func(closes []float64, period int) []float64 {
		ema := make([]float64, len(closes))
		alpha := 2.0 / float64(period+1)

		// Find first valid close to seed the EMA.
		seeded := false
		for i := 0; i < len(closes); i++ {
			if !seeded {
				if closes[i] > 0 {
					ema[i] = closes[i]
					seeded = true
				} else {
					ema[i] = math.NaN()
				}
			} else {
				if closes[i] > 0 {
					ema[i] = alpha*closes[i] + (1-alpha)*ema[i-1]
				} else {
					// Carry forward EMA when close is missing.
					ema[i] = ema[i-1]
				}
			}
		}
		return ema
	}

	// slopeAt computes the slope at position i as: (ema[i] - ema[i-lookback]) / ema[i-lookback] * 100 / lookback
	// This gives % change per day.
	slopeAt := func(ema []float64, i, lookback int) (float64, bool) {
		if i < lookback || i >= len(ema) {
			return 0, false
		}
		prev := ema[i-lookback]
		curr := ema[i]
		if math.IsNaN(prev) || math.IsNaN(curr) || prev <= 0 {
			return 0, false
		}
		return ((curr - prev) / prev * 100) / float64(lookback), true
	}

	latestIdx := actualDays - 1
	slopeLookback := 5 // measure slope over last 5 trading days
	accelLookback := 5 // compare slope now vs slope 5 days ago

	var results []EMASlopeResult

	for isin, s := range allSeries {
		// Count valid days.
		var count int
		for i := 0; i < actualDays; i++ {
			if s.closes[i] > 0 {
				count++
			}
		}
		if count < minDays {
			continue
		}

		// Compute EMAs.
		ema10 := computeEMA(s.closes, 10)
		ema20 := computeEMA(s.closes, 20)
		ema50 := computeEMA(s.closes, 50)

		// Current slopes.
		slope10, ok10 := slopeAt(ema10, latestIdx, slopeLookback)
		slope20, ok20 := slopeAt(ema20, latestIdx, slopeLookback)
		slope50, ok50 := slopeAt(ema50, latestIdx, slopeLookback)

		if !ok10 && !ok20 && !ok50 {
			continue
		}

		// Acceleration on the 10-EMA: current slope vs slope 5 days ago.
		var accel10 float64
		pastIdx := latestIdx - accelLookback
		if pastIdx >= slopeLookback {
			slopePast, okPast := slopeAt(ema10, pastIdx, slopeLookback)
			if okPast && ok10 {
				accel10 = slope10 - slopePast
			}
		}

		// Trend score: weighted blend of slopes.
		// 10-EMA slope gets 50% weight (most responsive),
		// 20-EMA gets 30%, 50-EMA gets 20%.
		var trendScore float64
		var totalWeight float64
		if ok10 {
			trendScore += slope10 * 0.50
			totalWeight += 0.50
		}
		if ok20 {
			trendScore += slope20 * 0.30
			totalWeight += 0.30
		}
		if ok50 {
			trendScore += slope50 * 0.20
			totalWeight += 0.20
		}
		if totalWeight > 0 {
			trendScore /= totalWeight
		}

		// Classify trend state.
		var trendState string
		switch {
		case trendScore > 0.15:
			trendState = "strong_up"
		case trendScore > 0.03:
			trendState = "up"
		case trendScore >= -0.03:
			trendState = "flat"
		case trendScore >= -0.15:
			trendState = "down"
		default:
			trendState = "strong_down"
		}

		results = append(results, EMASlopeResult{
			ISIN:       isin,
			Slope10D:   slope10,
			Slope20D:   slope20,
			Slope50D:   slope50,
			Accel10D:   accel10,
			TrendState: trendState,
			TrendScore: trendScore,
		})
	}

	// Sort by TrendScore descending (strongest uptrend first).
	sort.Slice(results, func(i, j int) bool {
		return results[i].TrendScore > results[j].TrendScore
	})

	log.Printf("Computed EMASlope for %d ISINs over %d trading days (coverage %.0f%%)", len(results), actualDays, minCoverage*100)
	return results, nil
}
