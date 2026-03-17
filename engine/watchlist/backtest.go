package watchlist

import (
	"database/sql"
	"fmt"
	"log"
	"sort"
	"time"
)

// BacktestConfig holds parameters for the rolling backtest.
type BacktestConfig struct {
	BuildConfig BuildConfig // which scoring config to test
	TopN        int         // how many top stocks to evaluate (default 25)
	StepDays    int         // build every N trading days (default 5)
	ForwardDays []int       // forward horizons to measure (default [1, 5])
	MinLookback int         // minimum trailing days for builder warm-up (default 60)
}

// DefaultBacktestConfig returns sensible defaults.
func DefaultBacktestConfig() BacktestConfig {
	cfg := DefaultConfig()
	cfg.SkipFreshness = true
	return BacktestConfig{
		BuildConfig: cfg,
		TopN:        25,
		StepDays:    1,
		ForwardDays: []int{1},
		MinLookback: 60,
	}
}

// BuildDateResult holds results for a single build date.
type BuildDateResult struct {
	BuildDate   string
	StocksBuilt int
	Horizons    []HorizonResult
}

// PickResult holds forward performance for a single stock pick.
type PickResult struct {
	ISIN     string  `json:"isin"`
	Rank     int     `json:"rank"`
	Score    float64 `json:"score"`
	Open     float64 `json:"open"`
	High     float64 `json:"high"`
	Low      float64 `json:"low"`
	Close    float64 `json:"close"`
	MaxOpp   float64 `json:"max_opp"`
	OCReturn float64 `json:"oc_return"`
	RangePct float64 `json:"range_pct"`
}

// HorizonResult holds forward performance stats for one horizon.
type HorizonResult struct {
	ForwardDays  int
	FwdDate      string
	AvgMaxOpp    float64 // avg (High - Open) / Open %
	AvgOCReturn  float64 // avg (Close - Open) / Open %
	AvgRange     float64 // avg (High - Low) / Low %
	HitRate      float64 // fraction with max opp > 0.5%
	MedianMaxOpp float64
	StocksEval   int // how many stocks had forward data
	Picks        []PickResult
	// Nifty 50 benchmark.
	NiftyAvgMaxOpp float64
	NiftyAvgRange  float64
}

// HorizonSummary aggregates across all build dates.
type HorizonSummary struct {
	ForwardDays    int
	NumBuildDates  int
	AvgMaxOpp      float64
	AvgOCReturn    float64
	AvgRange       float64
	AvgHitRate     float64
	NiftyAvgMaxOpp float64
	NiftyAvgRange  float64
	EdgeMaxOpp     float64
	EdgeRange      float64
}

// BacktestResult holds the full backtest output.
type BacktestResult struct {
	Config      BacktestConfig
	DateResults []BuildDateResult
	Summary     map[int]*HorizonSummary
}

// ohlcKey is the lookup key for OHLC data.
type ohlcKey struct {
	isin string
	date string
}

// ohlcRow holds OHLC data for a single stock-date.
type ohlcRow struct {
	Open  float64
	High  float64
	Low   float64
	Close float64
}

// RunBacktest executes a rolling historical backtest.
func RunBacktest(db *sql.DB, cfg BacktestConfig) (*BacktestResult, error) {
	// Get all trading dates oldest-first.
	allDates, err := getAllTradingDates(db)
	if err != nil {
		return nil, fmt.Errorf("fetching trading dates: %w", err)
	}

	maxFwd := 0
	for _, f := range cfg.ForwardDays {
		if f > maxFwd {
			maxFwd = f
		}
	}

	if len(allDates) < cfg.MinLookback+maxFwd+1 {
		return nil, fmt.Errorf("not enough data: have %d days, need %d+%d+1", len(allDates), cfg.MinLookback, maxFwd)
	}

	// Build date indices.
	var buildIndices []int
	for i := cfg.MinLookback; i < len(allDates)-maxFwd; i += cfg.StepDays {
		buildIndices = append(buildIndices, i)
	}

	log.Printf("Backtest: %d build dates, top-%d, horizons %v, data span %s → %s (%d days)",
		len(buildIndices), cfg.TopN, cfg.ForwardDays, allDates[0], allDates[len(allDates)-1], len(allDates))

	// Nifty 50 ISINs for benchmark.
	niftyISINs, err := getNiftyISINs(db)
	if err != nil {
		return nil, fmt.Errorf("fetching nifty ISINs: %w", err)
	}
	var niftyList []string
	for isin := range niftyISINs {
		niftyList = append(niftyList, isin)
	}

	// Preload ALL OHLC data into memory for fast forward lookups.
	log.Println("Preloading OHLC data...")
	ohlc, err := preloadOHLC(db)
	if err != nil {
		return nil, fmt.Errorf("preloading OHLC: %w", err)
	}
	log.Printf("Loaded %d OHLC records", len(ohlc))

	var dateResults []BuildDateResult

	for progIdx, buildIdx := range buildIndices {
		buildDate := allDates[buildIdx]
		log.Printf("  [%d/%d] Building at %s...", progIdx+1, len(buildIndices), buildDate)

		// Build watchlist using only data up to buildDate via table swap.
		result, err := buildAsOf(db, cfg.BuildConfig, buildDate)
		if err != nil {
			log.Printf("  SKIP %s: %v", buildDate, err)
			continue
		}
		if len(result.Qualified) == 0 {
			log.Printf("  SKIP %s: no qualified stocks", buildDate)
			continue
		}

		topN := cfg.TopN
		if topN > len(result.Qualified) {
			topN = len(result.Qualified)
		}
		topStocks := result.Qualified[:topN]
		topISINs := make([]string, topN)
		for i := 0; i < topN; i++ {
			topISINs[i] = topStocks[i].ISIN
		}

		// Evaluate forward horizons.
		var horizons []HorizonResult
		for _, fwd := range cfg.ForwardDays {
			fwdIdx := buildIdx + fwd
			if fwdIdx >= len(allDates) {
				continue
			}
			fwdDate := allDates[fwdIdx]

			hr := evaluateForwardWithPicks(topStocks, fwdDate, ohlc)
			hr.ForwardDays = fwd
			hr.FwdDate = fwdDate

			// Nifty benchmark.
			niftyHR := evaluateForward(niftyList, fwdDate, ohlc)
			hr.NiftyAvgMaxOpp = niftyHR.AvgMaxOpp
			hr.NiftyAvgRange = niftyHR.AvgRange

			horizons = append(horizons, hr)
		}

		dateResults = append(dateResults, BuildDateResult{
			BuildDate:   buildDate,
			StocksBuilt: len(result.Qualified),
			Horizons:    horizons,
		})
	}

	// Compute summary.
	summary := make(map[int]*HorizonSummary)
	for _, fwd := range cfg.ForwardDays {
		summary[fwd] = &HorizonSummary{ForwardDays: fwd}
	}
	for _, dr := range dateResults {
		for _, hr := range dr.Horizons {
			s := summary[hr.ForwardDays]
			s.NumBuildDates++
			s.AvgMaxOpp += hr.AvgMaxOpp
			s.AvgOCReturn += hr.AvgOCReturn
			s.AvgRange += hr.AvgRange
			s.AvgHitRate += hr.HitRate
			s.NiftyAvgMaxOpp += hr.NiftyAvgMaxOpp
			s.NiftyAvgRange += hr.NiftyAvgRange
		}
	}
	for _, s := range summary {
		if s.NumBuildDates > 0 {
			n := float64(s.NumBuildDates)
			s.AvgMaxOpp /= n
			s.AvgOCReturn /= n
			s.AvgRange /= n
			s.AvgHitRate /= n
			s.NiftyAvgMaxOpp /= n
			s.NiftyAvgRange /= n
			s.EdgeMaxOpp = s.AvgMaxOpp - s.NiftyAvgMaxOpp
			s.EdgeRange = s.AvgRange - s.NiftyAvgRange
		}
	}

	return &BacktestResult{
		Config:      cfg,
		DateResults: dateResults,
		Summary:     summary,
	}, nil
}

// buildAsOf constructs the watchlist using only data up to cutoffDate.
// Uses table swap: rename original → create filtered copy → build → restore.
func buildAsOf(db *sql.DB, cfg BuildConfig, cutoffDate string) (*BuildResult, error) {
	cfg.SkipFreshness = true

	// Step 1: Rename original table.
	if _, err := db.Exec(`ALTER TABLE nse_cm_bhavcopy RENAME TO _bhavcopy_full`); err != nil {
		return nil, fmt.Errorf("rename to backup: %w", err)
	}

	// Ensure we always restore, even on panic.
	restore := func() {
		db.Exec(`DROP TABLE IF EXISTS nse_cm_bhavcopy`)
		db.Exec(`ALTER TABLE _bhavcopy_full RENAME TO nse_cm_bhavcopy`)
	}
	defer restore()

	// Step 2: Create filtered table with only data up to cutoff.
	_, err := db.Exec(`CREATE TABLE nse_cm_bhavcopy AS SELECT * FROM _bhavcopy_full WHERE date <= $1::date`, cutoffDate)
	if err != nil {
		return nil, fmt.Errorf("create filtered table: %w", err)
	}

	// Step 3: Run build on filtered data.
	result, err := Build(db, cfg)
	if err != nil {
		return nil, fmt.Errorf("build at %s: %w", cutoffDate, err)
	}

	return result, nil
	// restore() runs via defer.
}

// getAllTradingDates returns all distinct trading dates oldest-first.
func getAllTradingDates(db *sql.DB) ([]string, error) {
	rows, err := db.Query(`SELECT DISTINCT date FROM nse_cm_bhavcopy ORDER BY date ASC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var dates []string
	for rows.Next() {
		var d time.Time
		if err := rows.Scan(&d); err != nil {
			return nil, err
		}
		dates = append(dates, d.Format("2006-01-02"))
	}
	return dates, rows.Err()
}

// getNiftyISINs returns Nifty 50 constituent ISINs.
func getNiftyISINs(db *sql.DB) (map[string]bool, error) {
	rows, err := db.Query(`SELECT isin FROM symbols WHERE 'NIFTY 50' = ANY(index_membership)`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make(map[string]bool)
	for rows.Next() {
		var isin string
		if err := rows.Scan(&isin); err != nil {
			return nil, err
		}
		result[isin] = true
	}
	return result, rows.Err()
}

// preloadOHLC loads all OHLC data into memory.
func preloadOHLC(db *sql.DB) (map[ohlcKey]ohlcRow, error) {
	rows, err := db.Query(`SELECT isin, date, open, high, low, close FROM nse_cm_bhavcopy`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	data := make(map[ohlcKey]ohlcRow, 350000)
	for rows.Next() {
		var isin string
		var dt time.Time
		var o, h, l, c float64
		if err := rows.Scan(&isin, &dt, &o, &h, &l, &c); err != nil {
			return nil, err
		}
		data[ohlcKey{isin, dt.Format("2006-01-02")}] = ohlcRow{o, h, l, c}
	}
	return data, rows.Err()
}

// evaluateForward computes intraday forward performance.
func evaluateForward(isins []string, fwdDate string, ohlc map[ohlcKey]ohlcRow) HorizonResult {
	var maxOpps, ocReturns, ranges []float64

	for _, isin := range isins {
		fwd, ok := ohlc[ohlcKey{isin, fwdDate}]
		if !ok || fwd.Open <= 0 || fwd.Low <= 0 {
			continue
		}
		maxOpps = append(maxOpps, (fwd.High-fwd.Open)/fwd.Open*100)
		ocReturns = append(ocReturns, (fwd.Close-fwd.Open)/fwd.Open*100)
		ranges = append(ranges, (fwd.High-fwd.Low)/fwd.Low*100)
	}

	if len(maxOpps) == 0 {
		return HorizonResult{}
	}

	n := float64(len(maxOpps))
	var sumMO, sumOC, sumR float64
	var hits int
	for i := range maxOpps {
		sumMO += maxOpps[i]
		sumOC += ocReturns[i]
		sumR += ranges[i]
		if maxOpps[i] > 0.5 {
			hits++
		}
	}

	sorted := make([]float64, len(maxOpps))
	copy(sorted, maxOpps)
	sort.Float64s(sorted)

	return HorizonResult{
		AvgMaxOpp:    sumMO / n,
		AvgOCReturn:  sumOC / n,
		AvgRange:     sumR / n,
		HitRate:      float64(hits) / n,
		MedianMaxOpp: sorted[len(sorted)/2],
		StocksEval:   len(maxOpps),
	}
}

// evaluateForwardWithPicks computes forward performance and returns per-pick detail.
func evaluateForwardWithPicks(stocks []StockScore, fwdDate string, ohlc map[ohlcKey]ohlcRow) HorizonResult {
	var maxOpps, ocReturns, ranges []float64
	var picks []PickResult

	for i, s := range stocks {
		fwd, ok := ohlc[ohlcKey{s.ISIN, fwdDate}]
		if !ok || fwd.Open <= 0 || fwd.Low <= 0 {
			continue
		}
		mo := (fwd.High - fwd.Open) / fwd.Open * 100
		oc := (fwd.Close - fwd.Open) / fwd.Open * 100
		rng := (fwd.High - fwd.Low) / fwd.Low * 100

		maxOpps = append(maxOpps, mo)
		ocReturns = append(ocReturns, oc)
		ranges = append(ranges, rng)

		picks = append(picks, PickResult{
			ISIN:     s.ISIN,
			Rank:     i + 1,
			Score:    s.Composite,
			Open:     fwd.Open,
			High:     fwd.High,
			Low:      fwd.Low,
			Close:    fwd.Close,
			MaxOpp:   mo,
			OCReturn: oc,
			RangePct: rng,
		})
	}

	if len(maxOpps) == 0 {
		return HorizonResult{}
	}

	n := float64(len(maxOpps))
	var sumMO, sumOC, sumR float64
	var hits int
	for i := range maxOpps {
		sumMO += maxOpps[i]
		sumOC += ocReturns[i]
		sumR += ranges[i]
		if maxOpps[i] > 0.5 {
			hits++
		}
	}

	sorted := make([]float64, len(maxOpps))
	copy(sorted, maxOpps)
	sort.Float64s(sorted)

	return HorizonResult{
		AvgMaxOpp:    sumMO / n,
		AvgOCReturn:  sumOC / n,
		AvgRange:     sumR / n,
		HitRate:      float64(hits) / n,
		MedianMaxOpp: sorted[len(sorted)/2],
		StocksEval:   len(maxOpps),
		Picks:        picks,
	}
}
