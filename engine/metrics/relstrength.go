package metrics

import (
	"database/sql"
	"fmt"
	"log"
	"sort"
	"time"

	"github.com/karanshergill/algotrix-go/utils"
)

// RelStrengthResult holds the relative strength of a single ISIN against the Nifty 50.
type RelStrengthResult struct {
	ISIN        string
	RS5D        float64 // 5-day relative strength (stock return - market return)
	RS10D       float64 // 10-day relative strength
	RS20D       float64 // 20-day relative strength
	RSComposite float64 // average of available RS timeframes
	TradingDays int     // number of days of data available
}

// ComputeRelStrength computes each stock's relative strength against a Nifty 50
// equal-weighted market proxy over the most recent trading days.
//   - days: how many recent trading days to look back (need at least 21)
//   - minCoverage: minimum fraction of days a stock must have data for (0.0 to 1.0)
func ComputeRelStrength(db *sql.DB, days int, minCoverage float64) ([]RelStrengthResult, error) {
	if days < 21 {
		return nil, fmt.Errorf("days must be at least 21 for relative strength, got %d", days)
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

	// --- Nifty 50 constituents ---
	niftyISINs := make(map[string]bool)
	nrows, err := db.Query(`SELECT isin FROM symbols WHERE 'NIFTY 50' = ANY(index_membership)`)
	if err != nil {
		return nil, fmt.Errorf("querying nifty 50 constituents: %w", err)
	}
	defer nrows.Close()
	for nrows.Next() {
		var isin string
		if err := nrows.Scan(&isin); err != nil {
			return nil, fmt.Errorf("scanning nifty isin: %w", err)
		}
		niftyISINs[isin] = true
	}
	if err := nrows.Err(); err != nil {
		return nil, fmt.Errorf("iterating nifty isins: %w", err)
	}
	if len(niftyISINs) == 0 {
		return nil, fmt.Errorf("no Nifty 50 constituents found")
	}

	// --- Fetch all closes ---
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

	// closes[isin][dateIdx] = close price (0 means missing).
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

	// --- Compute market (Nifty 50 equal-weighted) close series ---
	// For each date, average the closes of all Nifty 50 constituents that have data.
	marketCloses := make([]float64, actualDays)
	for i := 0; i < actualDays; i++ {
		var sum float64
		var count int
		for isin := range niftyISINs {
			s, ok := allSeries[isin]
			if !ok {
				continue
			}
			if s.closes[i] > 0 {
				sum += s.closes[i]
				count++
			}
		}
		if count > 0 {
			marketCloses[i] = sum / float64(count)
		}
	}

	// simpleReturn computes (latest / earlier) - 1 from positional indices.
	// Returns 0, false if either close is missing.
	simpleReturn := func(closes []float64, fromIdx, toIdx int) (float64, bool) {
		if fromIdx < 0 || fromIdx >= len(closes) || toIdx < 0 || toIdx >= len(closes) {
			return 0, false
		}
		if closes[fromIdx] <= 0 || closes[toIdx] <= 0 {
			return 0, false
		}
		return (closes[toIdx] / closes[fromIdx]) - 1, true
	}

	latestIdx := actualDays - 1
	minDays := int(float64(actualDays) * minCoverage)

	// Precompute market returns at each timeframe.
	type marketRet struct {
		val   float64
		valid bool
	}
	windows := []int{5, 10, 20}
	mktReturns := make([]marketRet, len(windows))
	for wi, w := range windows {
		fromIdx := latestIdx - w
		r, ok := simpleReturn(marketCloses, fromIdx, latestIdx)
		mktReturns[wi] = marketRet{val: r, valid: ok}
	}

	// --- Compute relative strength for each stock ---
	var results []RelStrengthResult

	for isin, s := range allSeries {
		// Count how many dates this stock has data for.
		var count int
		for i := 0; i < actualDays; i++ {
			if s.closes[i] > 0 {
				count++
			}
		}
		if count < minDays {
			continue
		}

		var rs5, rs10, rs20 float64
		var has5, has10, has20 bool
		var numTimeframes int
		var compositeSum float64

		for wi, w := range windows {
			if !mktReturns[wi].valid {
				continue
			}
			fromIdx := latestIdx - w
			stockRet, ok := simpleReturn(s.closes, fromIdx, latestIdx)
			if !ok {
				continue
			}
			rs := stockRet - mktReturns[wi].val
			switch w {
			case 5:
				rs5 = rs
				has5 = true
			case 10:
				rs10 = rs
				has10 = true
			case 20:
				rs20 = rs
				has20 = true
			}
			compositeSum += rs
			numTimeframes++
		}

		if numTimeframes == 0 {
			continue
		}

		result := RelStrengthResult{
			ISIN:        isin,
			RSComposite: compositeSum / float64(numTimeframes),
			TradingDays: count,
		}
		if has5 {
			result.RS5D = rs5
		}
		if has10 {
			result.RS10D = rs10
		}
		if has20 {
			result.RS20D = rs20
		}
		results = append(results, result)
	}

	// Sort by RSComposite descending.
	sort.Slice(results, func(i, j int) bool {
		return results[i].RSComposite > results[j].RSComposite
	})

	log.Printf("Computed RelStrength for %d ISINs over %d trading days (coverage %.0f%%)", len(results), actualDays, minCoverage*100)
	return results, nil
}
