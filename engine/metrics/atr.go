package metrics

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"

	"github.com/karanshergill/algotrix-go/utils"
)

// ATRResult holds ATR metrics for a single ISIN.
type ATRResult struct {
	ISIN        string
	SimpleATR   float64 // simple average of true ranges over lookback window
	WilderATR   float64 // Wilder's smoothed ATR
	ATRPct      float64 // WilderATR / avgClose * 100
	AvgClose    float64 // average close price over lookback window
	TradingDays int     // days of data within the lookback window
}

// ComputeATR computes Simple ATR and Wilder's smoothed ATR for all ISINs.
//   - lookbackDays: how many recent trading days to consider
//   - wilderPeriod: the N for Wilder's smoothing formula (default 14)
//   - minCoverage: minimum fraction of days a stock must have data for (0.0 to 1.0, default 1.0 = 100%)
//
// NOTE: No corporate action or bad prev_close handling yet — to be added when approved.
func ComputeATR(db *sql.DB, lookbackDays, wilderPeriod int, minCoverage float64) ([]ATRResult, error) {
	if lookbackDays <= 0 {
		return nil, fmt.Errorf("lookbackDays must be positive, got %d", lookbackDays)
	}
	if wilderPeriod <= 0 {
		return nil, fmt.Errorf("wilderPeriod must be positive, got %d", wilderPeriod)
	}
	if minCoverage <= 0 || minCoverage > 1.0 {
		minCoverage = 1.0
	}

	// Get the last N distinct trading dates.
	dates, err := utils.TradingDates(db, lookbackDays)
	if err != nil {
		return nil, fmt.Errorf("fetching trading dates: %w", err)
	}
	if len(dates) == 0 {
		return nil, nil
	}

	actualDays := len(dates)
	minDays := int(math.Ceil(float64(actualDays) * minCoverage))
	if minDays < wilderPeriod {
		minDays = wilderPeriod
	}

	// Fetch rows only for the exact dates in the lookback window.
	rows, err := db.Query(
		`SELECT isin, high, low, close, prev_close
		   FROM nse_cm_bhavcopy
		  WHERE date = ANY($1)
		  ORDER BY isin, date ASC`,
		dates,
	)
	if err != nil {
		return nil, fmt.Errorf("querying bhavcopy: %w", err)
	}
	defer rows.Close()

	type dayRow struct {
		high      float64
		low       float64
		close     float64
		prevClose float64
	}
	grouped := make(map[string][]dayRow)
	var order []string
	seen := make(map[string]bool)

	for rows.Next() {
		var isin string
		var high, low, cl, prevClose float64
		if err := rows.Scan(&isin, &high, &low, &cl, &prevClose); err != nil {
			return nil, fmt.Errorf("scanning row: %w", err)
		}
		if !seen[isin] {
			seen[isin] = true
			order = append(order, isin)
		}
		grouped[isin] = append(grouped[isin], dayRow{high, low, cl, prevClose})
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating rows: %w", err)
	}

	var results []ATRResult

	for _, isin := range order {
		data := grouped[isin]
		n := len(data)
		if n < minDays {
			continue
		}

		// Compute True Range for each day.
		trs := make([]float64, n)
		for i, d := range data {
			hl := d.high - d.low
			hpc := math.Abs(d.high - d.prevClose)
			lpc := math.Abs(d.low - d.prevClose)
			trs[i] = math.Max(hl, math.Max(hpc, lpc))
		}

		// Simple ATR: average of all true ranges in the window.
		var simpleSum float64
		for _, tr := range trs {
			simpleSum += tr
		}
		simpleATR := simpleSum / float64(n)

		// Wilder's ATR: seed with first wilderPeriod TRs, then smooth.
		var seedSum float64
		for i := 0; i < wilderPeriod && i < n; i++ {
			seedSum += trs[i]
		}
		wilderATR := seedSum / float64(wilderPeriod)
		for i := wilderPeriod; i < n; i++ {
			wilderATR = (wilderATR*float64(wilderPeriod-1) + trs[i]) / float64(wilderPeriod)
		}

		// Average close over the window.
		var closeSum float64
		for _, d := range data {
			closeSum += d.close
		}
		avgClose := closeSum / float64(n)

		var atrPct float64
		if avgClose > 0 {
			atrPct = wilderATR / avgClose * 100
		}

		results = append(results, ATRResult{
			ISIN:        isin,
			SimpleATR:   simpleATR,
			WilderATR:   wilderATR,
			ATRPct:      atrPct,
			AvgClose:    avgClose,
			TradingDays: n,
		})
	}

	sort.Slice(results, func(i, j int) bool {
		return results[i].ATRPct > results[j].ATRPct
	})

	log.Printf("Computed ATR for %d ISINs (lookback=%d, wilder=%d, coverage=%.0f%%)", len(results), lookbackDays, wilderPeriod, minCoverage*100)
	return results, nil
}
