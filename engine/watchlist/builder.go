package watchlist

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/karanshergill/algotrix-go/metrics"
)

// checkDataFreshness verifies that bhavcopy data is not stale.
// Uses the calendar table (which knows weekends AND market holidays)
// to determine the most recent expected trading day.
// Returns an error if data is more than 1 trading day old.
func checkDataFreshness(db *sql.DB) error {
	var latestBhavcopy time.Time
	err := db.QueryRow(`SELECT MAX(date) FROM nse_cm_bhavcopy`).Scan(&latestBhavcopy)
	if err != nil {
		return fmt.Errorf("checking data freshness: %w", err)
	}

	// IST timezone
	ist, _ := time.LoadLocation("Asia/Kolkata")
	now := time.Now().In(ist)
	today := now.Format("2006-01-02")

	// Use the calendar table to find the most recent trading day.
	// If before 18:30 IST, today's bhavcopy isn't available yet — look for the
	// most recent trading day strictly before today.
	// If after 18:30 IST, today's data should be available — include today.
	cutoff := time.Date(now.Year(), now.Month(), now.Day(), 18, 30, 0, 0, ist)

	var expectedDate time.Time
	if now.Before(cutoff) {
		// Today's data not yet available
		err = db.QueryRow(
			`SELECT date FROM calendar
			 WHERE is_trading_day = true AND date < $1::date
			 ORDER BY date DESC LIMIT 1`, today,
		).Scan(&expectedDate)
	} else {
		// Today's data should be available
		err = db.QueryRow(
			`SELECT date FROM calendar
			 WHERE is_trading_day = true AND date <= $1::date
			 ORDER BY date DESC LIMIT 1`, today,
		).Scan(&expectedDate)
	}
	if err != nil {
		// Calendar table might not exist or be empty — fall back to warning
		log.Printf("WARNING: could not query calendar table for freshness check: %v", err)
		return nil
	}

	// Count trading days between latest bhavcopy and expected date (exclusive of latest, inclusive of expected)
	latestStr := latestBhavcopy.Format("2006-01-02")
	expectedStr := expectedDate.Format("2006-01-02")

	var gap int
	err = db.QueryRow(
		`SELECT COUNT(*) FROM calendar
		 WHERE is_trading_day = true
		   AND date > $1::date
		   AND date <= $2::date`,
		latestStr, expectedStr,
	).Scan(&gap)
	if err != nil {
		log.Printf("WARNING: could not count trading day gap: %v", err)
		return nil
	}

	if gap > 1 {
		return fmt.Errorf(
			"STALE DATA: latest bhavcopy is %s (%d trading days behind expected %s). "+
				"Run 'bhavcopy fetch' to update before building",
			latestStr, gap, expectedStr,
		)
	}

	if gap == 1 {
		log.Printf("WARNING: bhavcopy data is 1 trading day behind (latest: %s, expected: %s). Proceeding.",
			latestStr, expectedStr,
		)
	}

	return nil
}

// BuildConfig holds all configurable parameters for watchlist construction.
type BuildConfig struct {
	// Lookback and coverage.
	LookbackDays int     // trading days to look back (default 30)
	MinCoverage  float64 // minimum fraction of days with data (default 1.0 = 100%)
	WilderPeriod int     // ATR Wilder's smoothing period (default 14)

	// Hard gates.
	MADTVFloor   float64 // minimum MADTV in rupees to qualify (default 1e9 = ₹100Cr)
	MinMarketCap float64 // in rupees, 0 = no filter
	MaxMarketCap float64 // in rupees, 0 = no upper limit

	// Scoring weights (must sum to 1.0).
	// Tradability layer (30%).
	WeightMADTV     float64 // default 0.08
	WeightAmihud    float64 // default 0.08
	WeightTradeSize float64 // default 0.07
	WeightATRPct    float64 // default 0.07
	// Opportunity layer (35%).
	WeightADRPct    float64 // default 0.10
	WeightRangeEff  float64 // default 0.10
	WeightParkinson float64 // default 0.08
	WeightMomentum  float64 // default 0.07
	// Market context layer (35%).
	WeightBeta     float64 // default 0.07
	WeightRS       float64 // default 0.08
	WeightGap      float64 // default 0.06
	WeightVolRatio float64 // default 0.07
	WeightEMASlope float64 // default 0.07

	// Optional composite score floor (0 = no floor).
	MinCompositeScore float64

	// Universe filter: if non-nil, only include these ISINs.
	UniverseISINs map[string]bool

	// SkipFreshness disables the data freshness check (used for historical backtesting).
	SkipFreshness bool
}

// DefaultConfig returns the default build configuration.
func DefaultConfig() BuildConfig {
	return BuildConfig{
		LookbackDays: 30,
		MinCoverage:  1.0,
		WilderPeriod: 14,
		MADTVFloor:   1e9, // ₹100 Crore
		// Tradability layer (30%).
		WeightMADTV:     0.08,
		WeightAmihud:    0.08,
		WeightTradeSize: 0.07,
		WeightATRPct:    0.07,
		// Opportunity layer (35%).
		WeightADRPct:    0.10,
		WeightRangeEff:  0.10,
		WeightParkinson: 0.08,
		WeightMomentum:  0.07,
		// Market context layer (35%).
		WeightBeta:     0.07,
		WeightRS:       0.08,
		WeightGap:      0.06,
		WeightVolRatio: 0.07,
		WeightEMASlope: 0.07,
		MinCompositeScore: 0,
	}
}

// LegacyConfig returns the old V2 weight configuration (8 metrics only)
// for benchmark comparisons. New metric weights are zeroed out.
func LegacyConfig() BuildConfig {
	cfg := BuildConfig{
		LookbackDays: 30,
		MinCoverage:  1.0,
		WilderPeriod: 14,
		MADTVFloor:   1e9,
		// Original V2 weights.
		WeightMADTV:     0.10,
		WeightAmihud:    0.10,
		WeightTradeSize: 0.10,
		WeightATRPct:    0.10,
		WeightADRPct:    0.18,
		WeightRangeEff:  0.17,
		WeightParkinson: 0.12,
		WeightMomentum:  0.13,
		// New metrics zeroed.
		WeightBeta:     0,
		WeightRS:       0,
		WeightGap:      0,
		WeightVolRatio: 0,
		WeightEMASlope: 0,
		MinCompositeScore: 0,
	}
	return cfg
}

// StockScore holds the per-metric percentile scores and composite for one ISIN.
type StockScore struct {
	ISIN string

	// Raw metric values.
	MADTV       float64
	Amihud      float64
	ATRPct      float64
	Parkinson   float64
	TradeSize   float64
	ADRPct      float64
	RangeEff    float64
	Momentum5D  float64
	TradingDays int

	MarketCap int64 // from symbols table

	// New metric raw values.
	Beta       float64
	BetaR2     float64
	RS         float64 // RS composite
	GapAvgAbs  float64
	VolRatio   float64
	TrendScore float64
	TrendState string

	// Percentile scores (0-100).
	PctMADTV     float64
	PctAmihud    float64 // inverted: lowest raw Amihud = 100
	PctATRPct    float64
	PctParkinson float64
	PctTradeSize float64
	PctADRPct    float64
	PctRangeEff  float64
	PctMomentum  float64

	// New metric percentiles.
	PctBeta     float64
	PctRS       float64
	PctGap      float64
	PctVolRatio float64
	PctEMASlope float64

	// Weighted composite score (0-100).
	Composite float64
}

// MetricStats holds distribution statistics for a single metric across the qualified pool.
type MetricStats struct {
	Min    float64 `json:"min"`
	P25    float64 `json:"p25"`
	Median float64 `json:"median"`
	P75    float64 `json:"p75"`
	Max    float64 `json:"max"`
}

// BuildResult holds the output of a watchlist build.
type BuildResult struct {
	Qualified []StockScore           // stocks that passed all gates, ranked by composite score
	Rejected  int                    // count of stocks rejected by hard gates
	Total     int                    // total stocks evaluated
	Stats     map[string]MetricStats // per-metric distribution stats across qualified pool
}

// Build constructs a watchlist by computing metrics, applying hard gates,
// percentile ranking, and composite scoring.
func Build(db *sql.DB, cfg BuildConfig) (*BuildResult, error) {
	// Pre-flight: check data freshness (skip for backtesting).
	if !cfg.SkipFreshness {
		if err := checkDataFreshness(db); err != nil {
			return nil, err
		}
	}

	// Compute all metrics.
	log.Println("Computing ADTV...")
	adtvResults, err := metrics.ComputeADTV(db, cfg.LookbackDays, cfg.MinCoverage)
	if err != nil {
		return nil, fmt.Errorf("computing ADTV: %w", err)
	}

	log.Println("Computing ATR...")
	atrResults, err := metrics.ComputeATR(db, cfg.LookbackDays, cfg.WilderPeriod, cfg.MinCoverage)
	if err != nil {
		return nil, fmt.Errorf("computing ATR: %w", err)
	}

	log.Println("Computing Amihud...")
	amihudResults, err := metrics.ComputeAmihud(db, cfg.LookbackDays, cfg.MinCoverage)
	if err != nil {
		return nil, fmt.Errorf("computing Amihud: %w", err)
	}

	log.Println("Computing Parkinson...")
	parkinsonResults, err := metrics.ComputeParkinson(db, cfg.LookbackDays, cfg.MinCoverage)
	if err != nil {
		return nil, fmt.Errorf("computing Parkinson: %w", err)
	}

	log.Println("Computing Trade Size...")
	tradeSizeResults, err := metrics.ComputeTradeSize(db, cfg.LookbackDays, cfg.MinCoverage)
	if err != nil {
		return nil, fmt.Errorf("computing Trade Size: %w", err)
	}

	log.Println("Computing ADR...")
	adrResults, err := metrics.ComputeADR(db, cfg.LookbackDays, cfg.MinCoverage)
	if err != nil {
		return nil, fmt.Errorf("computing ADR: %w", err)
	}

	log.Println("Computing Range Efficiency...")
	reResults, err := metrics.ComputeRangeEfficiency(db, cfg.LookbackDays, cfg.MinCoverage)
	if err != nil {
		return nil, fmt.Errorf("computing Range Efficiency: %w", err)
	}

	log.Println("Computing Momentum...")
	momResults, err := metrics.ComputeMomentum(db, cfg.LookbackDays, cfg.MinCoverage)
	if err != nil {
		return nil, fmt.Errorf("computing Momentum: %w", err)
	}

	// New market context metrics (use 60-day lookback for EMA/Beta warm-up).
	contextLookback := 60
	if cfg.LookbackDays > contextLookback {
		contextLookback = cfg.LookbackDays
	}

	log.Println("Computing Beta...")
	betaResults, err := metrics.ComputeBeta(db, contextLookback, 0.8)
	if err != nil {
		return nil, fmt.Errorf("computing Beta: %w", err)
	}

	log.Println("Computing Relative Strength...")
	rsResults, err := metrics.ComputeRelStrength(db, contextLookback, 0.8)
	if err != nil {
		return nil, fmt.Errorf("computing Relative Strength: %w", err)
	}

	log.Println("Computing Gap%...")
	gapResults, err := metrics.ComputeGap(db, cfg.LookbackDays, cfg.MinCoverage)
	if err != nil {
		return nil, fmt.Errorf("computing Gap: %w", err)
	}

	log.Println("Computing Volume Ratio...")
	vrResults, err := metrics.ComputeVolRatio(db, contextLookback, 0.8)
	if err != nil {
		return nil, fmt.Errorf("computing Volume Ratio: %w", err)
	}

	log.Println("Computing EMA Slope...")
	emaResults, err := metrics.ComputeEMASlope(db, contextLookback, 0.8)
	if err != nil {
		return nil, fmt.Errorf("computing EMA Slope: %w", err)
	}

	// Index metrics by ISIN for fast lookup.
	adtvMap := make(map[string]*metrics.ADTVResult)
	for i := range adtvResults {
		adtvMap[adtvResults[i].ISIN] = &adtvResults[i]
	}
	atrMap := make(map[string]*metrics.ATRResult)
	for i := range atrResults {
		atrMap[atrResults[i].ISIN] = &atrResults[i]
	}
	amihudMap := make(map[string]*metrics.AmihudResult)
	for i := range amihudResults {
		amihudMap[amihudResults[i].ISIN] = &amihudResults[i]
	}
	parkinsonMap := make(map[string]*metrics.ParkinsonResult)
	for i := range parkinsonResults {
		parkinsonMap[parkinsonResults[i].ISIN] = &parkinsonResults[i]
	}
	tradeSizeMap := make(map[string]*metrics.TradeSizeResult)
	for i := range tradeSizeResults {
		tradeSizeMap[tradeSizeResults[i].ISIN] = &tradeSizeResults[i]
	}
	adrMap := make(map[string]*metrics.ADRResult)
	for i := range adrResults {
		adrMap[adrResults[i].ISIN] = &adrResults[i]
	}
	reMap := make(map[string]*metrics.RangeEfficiencyResult)
	for i := range reResults {
		reMap[reResults[i].ISIN] = &reResults[i]
	}
	momMap := make(map[string]*metrics.MomentumResult)
	for i := range momResults {
		momMap[momResults[i].ISIN] = &momResults[i]
	}
	betaMap := make(map[string]*metrics.BetaResult)
	for i := range betaResults {
		betaMap[betaResults[i].ISIN] = &betaResults[i]
	}
	rsMap := make(map[string]*metrics.RelStrengthResult)
	for i := range rsResults {
		rsMap[rsResults[i].ISIN] = &rsResults[i]
	}
	gapMap := make(map[string]*metrics.GapResult)
	for i := range gapResults {
		gapMap[gapResults[i].ISIN] = &gapResults[i]
	}
	vrMap := make(map[string]*metrics.VolRatioResult)
	for i := range vrResults {
		vrMap[vrResults[i].ISIN] = &vrResults[i]
	}
	emaMap := make(map[string]*metrics.EMASlopeResult)
	for i := range emaResults {
		emaMap[emaResults[i].ISIN] = &emaResults[i]
	}

	// Collect all ISINs that have ADTV (base metric).
	allISINs := make(map[string]bool)
	for isin := range adtvMap {
		allISINs[isin] = true
	}

	// Fetch market cap from symbols table for market cap filtering.
	mcapMap := make(map[string]int64)
	{
		rows, err := db.Query(`SELECT isin, market_cap FROM symbols WHERE status = 'active'`)
		if err != nil {
			log.Printf("WARNING: could not fetch market cap data: %v", err)
		} else {
			defer rows.Close()
			for rows.Next() {
				var isin string
				var mc sql.NullInt64
				if err := rows.Scan(&isin, &mc); err == nil && mc.Valid {
					mcapMap[isin] = mc.Int64
				}
			}
		}
	}

	type candidate struct {
		isin       string
		madtv      float64
		amihud     float64
		atrPct     float64
		parkinson  float64
		tradeSize  float64
		adrPct     float64
		rangeEff   float64
		momentum5d float64
		days       int
		marketCap  int64
		// New market context metrics.
		beta       float64
		betaR2     float64
		rs         float64 // RS composite
		gapAvgAbs  float64
		volRatio   float64
		trendScore float64
		trendState string
	}

	var candidates []candidate
	rejected := 0
	total := 0

	// Check if new metrics have any weight (for backward compat with legacy configs).
	needNewMetrics := cfg.WeightBeta > 0 || cfg.WeightRS > 0 || cfg.WeightGap > 0 ||
		cfg.WeightVolRatio > 0 || cfg.WeightEMASlope > 0

	for isin := range allISINs {
		// Universe filter.
		if cfg.UniverseISINs != nil && !cfg.UniverseISINs[isin] {
			continue
		}

		total++

		// Hard gate: exclude ETFs/MFs (ISIN prefix INF).
		if strings.HasPrefix(isin, "INF") {
			rejected++
			continue
		}

		// All 8 base metrics must exist.
		adtv, ok1 := adtvMap[isin]
		atr, ok2 := atrMap[isin]
		amihud, ok3 := amihudMap[isin]
		park, ok4 := parkinsonMap[isin]
		ts, ok5 := tradeSizeMap[isin]
		adr, ok6 := adrMap[isin]
		re, ok7 := reMap[isin]
		mom, ok8 := momMap[isin]
		if !ok1 || !ok2 || !ok3 || !ok4 || !ok5 || !ok6 || !ok7 || !ok8 {
			rejected++
			continue
		}

		// New metrics: optional — if weighted, they must exist; if zero-weighted, use defaults.
		var betaVal, betaR2Val, rsVal, gapVal, vrVal, tscore float64
		var tstate string
		if needNewMetrics {
			b, okB := betaMap[isin]
			r, okR := rsMap[isin]
			g, okG := gapMap[isin]
			v, okV := vrMap[isin]
			e, okE := emaMap[isin]
			if !okB || !okR || !okG || !okV || !okE {
				rejected++
				continue
			}
			betaVal = b.Beta
			betaR2Val = b.RSquared
			rsVal = r.RSComposite
			gapVal = g.AvgAbsGapPct
			vrVal = v.VolumeRatio
			tscore = e.TrendScore
			tstate = e.TrendState
		}

		// NOTE: MADTV floor is applied AFTER scoring (post-filter) so that
		// percentile ranks are computed against the full eligible universe.
		// This keeps scores stable regardless of the floor setting.

		candidates = append(candidates, candidate{
			isin:       isin,
			madtv:      adtv.MADTV,
			marketCap:  mcapMap[isin],
			amihud:     amihud.Amihud,
			atrPct:     atr.ATRPct,
			parkinson:  park.ParkinsonDaily,
			tradeSize:  ts.AvgTradeSize,
			adrPct:     adr.ADRPct,
			rangeEff:   re.AvgRangeEfficiency,
			momentum5d: mom.Return5D,
			days:       adtv.TradingDays,
			beta:       betaVal,
			betaR2:     betaR2Val,
			rs:         rsVal,
			gapAvgAbs:  gapVal,
			volRatio:   vrVal,
			trendScore: tscore,
			trendState: tstate,
		})
	}

	if len(candidates) == 0 {
		return &BuildResult{Rejected: rejected, Total: total}, nil
	}

	// Compute percentile ranks.
	n := len(candidates)

	// Extract raw values for each metric.
	madtvVals := make([]float64, n)
	amihudVals := make([]float64, n)
	atrPctVals := make([]float64, n)
	parkVals := make([]float64, n)
	tsVals := make([]float64, n)
	adrPctVals := make([]float64, n)
	reVals := make([]float64, n)
	momVals := make([]float64, n)
	betaVals := make([]float64, n)
	rsVals := make([]float64, n)
	gapVals := make([]float64, n)
	vrVals := make([]float64, n)
	emaVals := make([]float64, n)
	for i, c := range candidates {
		madtvVals[i] = c.madtv
		amihudVals[i] = c.amihud
		atrPctVals[i] = c.atrPct
		parkVals[i] = c.parkinson
		tsVals[i] = c.tradeSize
		adrPctVals[i] = c.adrPct
		reVals[i] = c.rangeEff
		// Momentum uses absolute value — strong movers in either direction score high.
		momVals[i] = math.Abs(c.momentum5d)
		// Beta: higher = more reactive to market. Use abs for ranking (negative beta is also interesting).
		betaVals[i] = math.Abs(c.beta)
		// RS composite: higher = stronger relative performance.
		rsVals[i] = c.rs
		// Gap: higher avg abs gap = more overnight opportunity/risk.
		gapVals[i] = c.gapAvgAbs
		// Volume Ratio: higher = more surge activity.
		vrVals[i] = c.volRatio
		// EMA Slope trend score: use absolute value (strong trend in either direction is tradable).
		emaVals[i] = math.Abs(c.trendScore)
	}

	pctMADTV := percentileRank(madtvVals, false)       // higher = better
	pctAmihud := percentileRank(amihudVals, true)       // lower raw = better → invert
	pctATRPct := percentileRank(atrPctVals, false)      // higher = better
	pctParkinson := percentileRank(parkVals, false)      // higher = better
	pctTradeSize := percentileRank(tsVals, false)        // higher = better
	pctADRPct := percentileRank(adrPctVals, false)       // higher = better
	pctRangeEff := percentileRank(reVals, false)         // higher = better
	pctMomentum := percentileRank(momVals, false)        // higher abs = better
	pctBeta := percentileRank(betaVals, false)           // higher abs beta = more reactive
	pctRS := percentileRank(rsVals, false)               // higher RS = outperforming
	pctGap := percentileRank(gapVals, false)             // higher gap = more opportunity
	pctVolRatio := percentileRank(vrVals, false)         // higher VR = more surge
	pctEMASlope := percentileRank(emaVals, false)        // higher abs trend = stronger trend

	// Build scored results.
	var scored []StockScore
	for i, c := range candidates {
		composite := cfg.WeightMADTV*pctMADTV[i] +
			cfg.WeightAmihud*pctAmihud[i] +
			cfg.WeightATRPct*pctATRPct[i] +
			cfg.WeightParkinson*pctParkinson[i] +
			cfg.WeightTradeSize*pctTradeSize[i] +
			cfg.WeightADRPct*pctADRPct[i] +
			cfg.WeightRangeEff*pctRangeEff[i] +
			cfg.WeightMomentum*pctMomentum[i] +
			cfg.WeightBeta*pctBeta[i] +
			cfg.WeightRS*pctRS[i] +
			cfg.WeightGap*pctGap[i] +
			cfg.WeightVolRatio*pctVolRatio[i] +
			cfg.WeightEMASlope*pctEMASlope[i]

		// Optional composite score floor.
		if cfg.MinCompositeScore > 0 && composite < cfg.MinCompositeScore {
			rejected++
			continue
		}

		// Post-score filter: MADTV floor (applied after percentile computation
		// so scores remain stable regardless of floor setting).
		if cfg.MADTVFloor > 0 && c.madtv < cfg.MADTVFloor {
			rejected++
			continue
		}

		// Post-score filter: market cap range.
		if cfg.MinMarketCap > 0 && float64(c.marketCap) < cfg.MinMarketCap {
			rejected++
			continue
		}
		if cfg.MaxMarketCap > 0 && float64(c.marketCap) > cfg.MaxMarketCap {
			rejected++
			continue
		}

		scored = append(scored, StockScore{
			ISIN:         c.isin,
			MADTV:        c.madtv,
			MarketCap:    c.marketCap,
			Amihud:       c.amihud,
			ATRPct:       c.atrPct,
			Parkinson:    c.parkinson,
			TradeSize:    c.tradeSize,
			ADRPct:       c.adrPct,
			RangeEff:     c.rangeEff,
			Momentum5D:   c.momentum5d,
			TradingDays:  c.days,
			Beta:         c.beta,
			BetaR2:       c.betaR2,
			RS:           c.rs,
			GapAvgAbs:    c.gapAvgAbs,
			VolRatio:     c.volRatio,
			TrendScore:   c.trendScore,
			TrendState:   c.trendState,
			PctMADTV:     pctMADTV[i],
			PctAmihud:    pctAmihud[i],
			PctATRPct:    pctATRPct[i],
			PctParkinson: pctParkinson[i],
			PctTradeSize: pctTradeSize[i],
			PctADRPct:    pctADRPct[i],
			PctRangeEff:  pctRangeEff[i],
			PctMomentum:  pctMomentum[i],
			PctBeta:      pctBeta[i],
			PctRS:        pctRS[i],
			PctGap:       pctGap[i],
			PctVolRatio:  pctVolRatio[i],
			PctEMASlope:  pctEMASlope[i],
			Composite:    composite,
		})
	}

	// Sort by composite score descending.
	sort.Slice(scored, func(i, j int) bool {
		return scored[i].Composite > scored[j].Composite
	})

	log.Printf("Watchlist built: %d qualified out of %d evaluated (%d rejected)",
		len(scored), total, rejected)

	// Compute distribution stats across qualified pool.
	stats := computeMetricStats(scored)

	return &BuildResult{
		Qualified: scored,
		Rejected:  rejected,
		Total:     total,
		Stats:     stats,
	}, nil
}

// computeMetricStats computes min/p25/median/p75/max for each raw metric
// across the qualified stocks. Percentiles use nearest-rank interpolation.
func computeMetricStats(stocks []StockScore) map[string]MetricStats {
	if len(stocks) == 0 {
		return nil
	}

	extractors := map[string]func(s StockScore) float64{
		"madtv":      func(s StockScore) float64 { return s.MADTV },
		"amihud":     func(s StockScore) float64 { return s.Amihud },
		"atrPct":     func(s StockScore) float64 { return s.ATRPct },
		"parkinson":  func(s StockScore) float64 { return s.Parkinson },
		"tradeSize":  func(s StockScore) float64 { return s.TradeSize },
		"adrPct":     func(s StockScore) float64 { return s.ADRPct },
		"rangeEff":   func(s StockScore) float64 { return s.RangeEff },
		"momentum":   func(s StockScore) float64 { return math.Abs(s.Momentum5D) },
		"beta":       func(s StockScore) float64 { return math.Abs(s.Beta) },
		"rs":         func(s StockScore) float64 { return s.RS },
		"gapAvgAbs":  func(s StockScore) float64 { return s.GapAvgAbs },
		"volRatio":   func(s StockScore) float64 { return s.VolRatio },
		"trendScore": func(s StockScore) float64 { return math.Abs(s.TrendScore) },
	}

	result := make(map[string]MetricStats, len(extractors))
	for name, extract := range extractors {
		vals := make([]float64, len(stocks))
		for i, s := range stocks {
			vals[i] = extract(s)
		}
		sort.Float64s(vals)
		n := len(vals)
		result[name] = MetricStats{
			Min:    vals[0],
			P25:    percentileValue(vals, 25),
			Median: percentileValue(vals, 50),
			P75:    percentileValue(vals, 75),
			Max:    vals[n-1],
		}
	}
	return result
}

// percentileValue returns the value at the given percentile (0-100) using
// linear interpolation between nearest ranks.
func percentileValue(sorted []float64, pct float64) float64 {
	n := len(sorted)
	if n == 0 {
		return 0
	}
	if n == 1 {
		return sorted[0]
	}
	// Rank position (0-indexed, fractional).
	pos := (pct / 100) * float64(n-1)
	lo := int(math.Floor(pos))
	hi := int(math.Ceil(pos))
	if lo == hi || hi >= n {
		return sorted[lo]
	}
	frac := pos - float64(lo)
	return sorted[lo]*(1-frac) + sorted[hi]*frac
}

// percentileRank computes percentile ranks (0-100) for a slice of values.
// If invert is true, lower raw values get higher percentile (used for Amihud).
func percentileRank(vals []float64, invert bool) []float64 {
	n := len(vals)
	if n == 0 {
		return nil
	}

	// Create index-value pairs for sorting.
	type iv struct {
		idx int
		val float64
	}
	pairs := make([]iv, n)
	for i, v := range vals {
		pairs[i] = iv{i, v}
	}

	// Sort ascending by value.
	sort.Slice(pairs, func(i, j int) bool {
		return pairs[i].val < pairs[j].val
	})

	// Assign ranks (handle ties with average rank).
	ranks := make([]float64, n)
	i := 0
	for i < n {
		j := i
		// Find all items with the same value.
		for j < n && math.Abs(pairs[j].val-pairs[i].val) < 1e-15 {
			j++
		}
		// Average rank for ties.
		avgRank := float64(i+j-1) / 2.0
		for k := i; k < j; k++ {
			ranks[pairs[k].idx] = avgRank
		}
		i = j
	}

	// Convert ranks to percentiles (0-100).
	result := make([]float64, n)
	for idx, rank := range ranks {
		pct := rank / float64(n-1) * 100
		if invert {
			pct = 100 - pct
		}
		result[idx] = pct
	}

	// Edge case: single item.
	if n == 1 {
		result[0] = 50 // median by definition
	}

	return result
}
