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

// BetaResult holds the beta coefficient of a single ISIN against the Nifty 50.
type BetaResult struct {
	ISIN        string
	Beta        float64 // beta coefficient vs Nifty 50
	RSquared    float64 // R² of the regression (goodness of fit)
	TradingDays int     // number of overlapping days used
}

// ComputeBeta computes each stock's beta against a Nifty 50 equal-weighted
// market proxy over the most recent trading days.
//   - days: how many recent trading days to look back
//   - minCoverage: minimum fraction of days a stock must overlap with the market (0.0 to 1.0)
func ComputeBeta(db *sql.DB, days int, minCoverage float64) ([]BetaResult, error) {
	if days < 2 {
		return nil, fmt.Errorf("days must be at least 2 for beta, got %d", days)
	}
	if minCoverage <= 0 || minCoverage > 1.0 {
		minCoverage = 1.0
	}

	dates, err := utils.TradingDates(db, days)
	if err != nil {
		return nil, fmt.Errorf("fetching trading dates: %w", err)
	}
	if len(dates) < 2 {
		return nil, nil
	}

	actualDays := len(dates)
	// We lose one day computing returns, so base minDays on return count.
	returnDays := actualDays - 1
	minDays := int(math.Ceil(float64(returnDays) * minCoverage))

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
		// Parse and re-format to cover both long and short forms.
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

	// --- Compute daily log returns per ISIN ---
	// returns[isin][i] corresponds to the return from date i to date i+1.
	// A NaN signals a missing return.
	type returnSeries struct {
		returns []float64
	}
	allReturns := make(map[string]*returnSeries, len(allSeries))

	for isin, s := range allSeries {
		rs := &returnSeries{returns: make([]float64, returnDays)}
		for i := 0; i < returnDays; i++ {
			prev := s.closes[i]
			curr := s.closes[i+1]
			if prev > 0 && curr > 0 {
				rs.returns[i] = math.Log(curr / prev)
			} else {
				rs.returns[i] = math.NaN()
			}
		}
		allReturns[isin] = rs
	}

	// --- Market return: equal-weighted avg of Nifty 50 constituents per day ---
	marketReturns := make([]float64, returnDays)
	marketValid := make([]bool, returnDays)

	for i := 0; i < returnDays; i++ {
		var sum float64
		var count int
		for isin := range niftyISINs {
			rs, ok := allReturns[isin]
			if !ok {
				continue
			}
			r := rs.returns[i]
			if !math.IsNaN(r) {
				sum += r
				count++
			}
		}
		if count > 0 {
			marketReturns[i] = sum / float64(count)
			marketValid[i] = true
		}
	}

	// --- Compute beta for each stock ---
	var results []BetaResult

	for isin, rs := range allReturns {
		// Collect paired (stock, market) returns where both are valid.
		var sr, mr []float64
		for i := 0; i < returnDays; i++ {
			if !marketValid[i] || math.IsNaN(rs.returns[i]) {
				continue
			}
			sr = append(sr, rs.returns[i])
			mr = append(mr, marketReturns[i])
		}
		if len(sr) < minDays {
			continue
		}

		// Mean.
		var sumS, sumM float64
		n := float64(len(sr))
		for k := range sr {
			sumS += sr[k]
			sumM += mr[k]
		}
		meanS := sumS / n
		meanM := sumM / n

		// Cov(stock, market), Var(market), Var(stock).
		var cov, varM, varS float64
		for k := range sr {
			ds := sr[k] - meanS
			dm := mr[k] - meanM
			cov += ds * dm
			varM += dm * dm
			varS += ds * ds
		}
		cov /= n
		varM /= n
		varS /= n

		if varM < 1e-18 {
			continue // market variance essentially zero
		}

		beta := cov / varM

		// R² = Corr²  = Cov² / (Var_s * Var_m)
		var rSquared float64
		if varS > 1e-18 {
			rSquared = (cov * cov) / (varS * varM)
		}

		results = append(results, BetaResult{
			ISIN:        isin,
			Beta:        beta,
			RSquared:    rSquared,
			TradingDays: len(sr),
		})
	}

	// Sort by Beta descending (highest beta first).
	sort.Slice(results, func(i, j int) bool {
		return results[i].Beta > results[j].Beta
	})

	log.Printf("Computed Beta for %d ISINs over %d trading days (coverage %.0f%%)", len(results), actualDays, minCoverage*100)
	return results, nil
}
