package watchlist

import (
	"database/sql"
	"fmt"
	"log"
	"math"
	"sort"
	"strings"

	"github.com/karanshergill/algotrix-go/metrics"
)

// BuildConfig holds all configurable parameters for watchlist construction.
type BuildConfig struct {
	// Lookback and coverage.
	LookbackDays int     // trading days to look back (default 30)
	MinCoverage  float64 // minimum fraction of days with data (default 1.0 = 100%)
	WilderPeriod int     // ATR Wilder's smoothing period (default 14)

	// Hard gates.
	MADTVFloor float64 // minimum MADTV in rupees to qualify (default 1e9 = ₹100Cr)

	// Scoring weights (must sum to 1.0).
	// Tradability layer (40%).
	WeightMADTV     float64 // default 0.10
	WeightAmihud    float64 // default 0.10
	WeightTradeSize float64 // default 0.10
	WeightATRPct    float64 // default 0.10
	// Opportunity layer (60%).
	WeightADRPct        float64 // default 0.15
	WeightRangeEff      float64 // default 0.15
	WeightParkinson     float64 // default 0.10
	WeightMomentum      float64 // default 0.10

	// Optional composite score floor (0 = no floor).
	MinCompositeScore float64

	// Universe filter: if non-nil, only include these ISINs.
	UniverseISINs map[string]bool
}

// DefaultConfig returns the default build configuration.
func DefaultConfig() BuildConfig {
	return BuildConfig{
		LookbackDays: 30,
		MinCoverage:  1.0,
		WilderPeriod: 14,
		MADTVFloor:   1e9, // ₹100 Crore
		// Tradability layer (40%).
		WeightMADTV:     0.10,
		WeightAmihud:    0.10,
		WeightTradeSize: 0.10,
		WeightATRPct:    0.10,
		// Opportunity layer (60%).
		WeightADRPct:      0.18,
		WeightRangeEff:    0.17,
		WeightParkinson:   0.12,
		WeightMomentum:    0.13,
		MinCompositeScore: 0,
	}
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

	// Percentile scores (0-100).
	PctMADTV     float64
	PctAmihud    float64 // inverted: lowest raw Amihud = 100
	PctATRPct    float64
	PctParkinson float64
	PctTradeSize float64
	PctADRPct    float64
	PctRangeEff  float64
	PctMomentum  float64

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

	// Collect all ISINs that have ADTV (base metric).
	allISINs := make(map[string]bool)
	for isin := range adtvMap {
		allISINs[isin] = true
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
	}

	var candidates []candidate
	rejected := 0
	total := 0

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

		// All 8 metrics must exist.
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

		// NOTE: MADTV floor is applied AFTER scoring (post-filter) so that
		// percentile ranks are computed against the full eligible universe.
		// This keeps scores stable regardless of the floor setting.

		candidates = append(candidates, candidate{
			isin:       isin,
			madtv:      adtv.MADTV,
			amihud:     amihud.Amihud,
			atrPct:     atr.ATRPct,
			parkinson:  park.ParkinsonDaily,
			tradeSize:  ts.AvgTradeSize,
			adrPct:     adr.ADRPct,
			rangeEff:   re.AvgRangeEfficiency,
			momentum5d: mom.Return5D,
			days:       adtv.TradingDays,
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
	}

	pctMADTV := percentileRank(madtvVals, false)      // higher = better
	pctAmihud := percentileRank(amihudVals, true)      // lower raw = better → invert
	pctATRPct := percentileRank(atrPctVals, false)     // higher = better
	pctParkinson := percentileRank(parkVals, false)     // higher = better
	pctTradeSize := percentileRank(tsVals, false)       // higher = better
	pctADRPct := percentileRank(adrPctVals, false)      // higher = better
	pctRangeEff := percentileRank(reVals, false)        // higher = better
	pctMomentum := percentileRank(momVals, false)       // higher abs = better

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
			cfg.WeightMomentum*pctMomentum[i]

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

		scored = append(scored, StockScore{
			ISIN:         c.isin,
			MADTV:        c.madtv,
			Amihud:       c.amihud,
			ATRPct:       c.atrPct,
			Parkinson:    c.parkinson,
			TradeSize:    c.tradeSize,
			ADRPct:       c.adrPct,
			RangeEff:     c.rangeEff,
			Momentum5D:   c.momentum5d,
			TradingDays:  c.days,
			PctMADTV:     pctMADTV[i],
			PctAmihud:    pctAmihud[i],
			PctATRPct:    pctATRPct[i],
			PctParkinson: pctParkinson[i],
			PctTradeSize: pctTradeSize[i],
			PctADRPct:    pctADRPct[i],
			PctRangeEff:  pctRangeEff[i],
			PctMomentum:  pctMomentum[i],
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
		"madtv":     func(s StockScore) float64 { return s.MADTV },
		"amihud":    func(s StockScore) float64 { return s.Amihud },
		"atrPct":    func(s StockScore) float64 { return s.ATRPct },
		"parkinson": func(s StockScore) float64 { return s.Parkinson },
		"tradeSize": func(s StockScore) float64 { return s.TradeSize },
		"adrPct":    func(s StockScore) float64 { return s.ADRPct },
		"rangeEff":  func(s StockScore) float64 { return s.RangeEff },
		"momentum":  func(s StockScore) float64 { return math.Abs(s.Momentum5D) },
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
