package features

import (
	"context"
	"fmt"
	"log"
	"math"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// ---------------------------------------------------------------------------
// PreloadBaselines loads historical data from atdb into StockState fields.
// Must be called before the engine starts processing ticks.
// ---------------------------------------------------------------------------

func PreloadBaselines(ctx context.Context, pool *pgxpool.Pool, stocks map[string]*StockState, sectors map[string]*SectorState) error {
	// Get last 15 trading dates (covers 14-day ATR + 1 for prev close)
	tradingDates, err := queryTradingDates(ctx, pool, 15)
	if err != nil {
		return fmt.Errorf("queryTradingDates: %w", err)
	}
	if len(tradingDates) == 0 {
		log.Println("[baselines] WARNING: no trading dates found in nse_cm_bhavcopy")
		return nil
	}

	lastDate := tradingDates[0]
	tenthIdx := min(9, len(tradingDates)-1)
	fourteenthIdx := min(13, len(tradingDates)-1)
	tenthDate := tradingDates[tenthIdx]
	fourteenthDate := tradingDates[fourteenthIdx]

	log.Printf("[baselines] trading dates: last=%s, 10th=%s, 14th=%s (found %d dates)",
		lastDate.Format("2006-01-02"), tenthDate.Format("2006-01-02"),
		fourteenthDate.Format("2006-01-02"), len(tradingDates))

	// 1. Previous close
	if err := loadPrevClose(ctx, pool, stocks, lastDate); err != nil {
		return fmt.Errorf("loadPrevClose: %w", err)
	}

	// 2. 14-trading-day ATR
	if err := loadATR(ctx, pool, stocks, tradingDates, fourteenthIdx); err != nil {
		return fmt.Errorf("loadATR: %w", err)
	}

	// 3. Volume slot baselines (mean + stddev + samples per 5-min slot)
	if err := loadVolumeSlotBaselines(ctx, pool, stocks, tenthDate); err != nil {
		return fmt.Errorf("loadVolumeSlotBaselines: %w", err)
	}

	// 4. 10-trading-day average daily volume
	if err := loadAvgDailyVolume(ctx, pool, stocks, tenthDate); err != nil {
		return fmt.Errorf("loadAvgDailyVolume: %w", err)
	}

	// 5. Sector membership mapping
	loadSectorMapping(stocks, sectors)

	return nil
}

// queryTradingDates returns the last N trading dates from nse_cm_bhavcopy,
// ordered most recent first.
func queryTradingDates(ctx context.Context, pool *pgxpool.Pool, n int) ([]time.Time, error) {
	rows, err := pool.Query(ctx,
		`SELECT DISTINCT trade_date FROM nse_cm_bhavcopy ORDER BY trade_date DESC LIMIT $1`, n)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var dates []time.Time
	for rows.Next() {
		var d time.Time
		if err := rows.Scan(&d); err != nil {
			return nil, err
		}
		dates = append(dates, d)
	}
	return dates, rows.Err()
}

// loadPrevClose loads previous close price for each stock from nse_cm_bhavcopy.
func loadPrevClose(ctx context.Context, pool *pgxpool.Pool, stocks map[string]*StockState, lastDate time.Time) error {
	rows, err := pool.Query(ctx,
		`SELECT isin, close FROM nse_cm_bhavcopy WHERE trade_date = $1`, lastDate)
	if err != nil {
		return err
	}
	defer rows.Close()

	loaded := 0
	for rows.Next() {
		var isin string
		var closePrice float64
		if err := rows.Scan(&isin, &closePrice); err != nil {
			return err
		}
		if s, ok := stocks[isin]; ok {
			s.PrevClose = closePrice
			loaded++
		}
	}
	log.Printf("[baselines] loadPrevClose: %d stocks loaded", loaded)
	return rows.Err()
}

// loadATR computes 14-trading-day ATR for each stock.
// TR = max(high-low, |high-prevClose|, |low-prevClose|)
// ATR = mean(TR over N days)
func loadATR(ctx context.Context, pool *pgxpool.Pool, stocks map[string]*StockState, tradingDates []time.Time, fourteenthIdx int) error {
	// We need OHLC data from the oldest date up to the most recent
	oldestDate := tradingDates[fourteenthIdx]

	rows, err := pool.Query(ctx,
		`SELECT isin, trade_date, open, high, low, close, prev_close
		 FROM nse_cm_bhavcopy
		 WHERE trade_date >= $1
		 ORDER BY isin, trade_date`, oldestDate)
	if err != nil {
		return err
	}
	defer rows.Close()

	// Collect OHLC rows grouped by ISIN
	type ohlcRow struct {
		high, low, prevClose float64
	}
	isinRows := make(map[string][]ohlcRow)

	for rows.Next() {
		var isin, tradeDate string
		var open, high, low, close, prevClose float64
		if err := rows.Scan(&isin, &tradeDate, &open, &high, &low, &close, &prevClose); err != nil {
			return err
		}
		if _, ok := stocks[isin]; ok {
			isinRows[isin] = append(isinRows[isin], ohlcRow{high: high, low: low, prevClose: prevClose})
		}
	}
	if err := rows.Err(); err != nil {
		return err
	}

	loaded := 0
	for isin, ohlcData := range isinRows {
		if len(ohlcData) == 0 {
			continue
		}
		var trSum float64
		for _, r := range ohlcData {
			hl := r.high - r.low
			hpc := math.Abs(r.high - r.prevClose)
			lpc := math.Abs(r.low - r.prevClose)
			tr := math.Max(hl, math.Max(hpc, lpc))
			trSum += tr
		}
		stocks[isin].ATR14d = trSum / float64(len(ohlcData))
		loaded++
	}
	log.Printf("[baselines] loadATR: %d stocks loaded", loaded)
	return nil
}

// loadVolumeSlotBaselines computes per-5-min-slot mean + stddev of volume
// from nse_cm_ticks over last 10 trading days.
func loadVolumeSlotBaselines(ctx context.Context, pool *pgxpool.Pool, stocks map[string]*StockState, sinceDate time.Time) error {
	// Query tick data grouped by ISIN and 5-min slot across trading days.
	// We compute slot from the timestamp, then aggregate volume deltas per slot per day.
	// Since nse_cm_ticks has cumulative volume, we need to compute deltas.
	//
	// Strategy: use a SQL subquery to compute per-slot volume, then aggregate.
	rows, err := pool.Query(ctx,
		`WITH tick_slots AS (
			SELECT
				isin,
				ts::date AS trade_day,
				FLOOR(EXTRACT(EPOCH FROM (ts::time - '09:15:00'::time)) / 300)::int AS slot,
				MAX(volume) - MIN(volume) AS slot_volume
			FROM nse_cm_ticks
			WHERE ts >= $1
			  AND ts::time >= '09:15:00'
			  AND ts::time < '15:30:00'
			GROUP BY isin, ts::date, FLOOR(EXTRACT(EPOCH FROM (ts::time - '09:15:00'::time)) / 300)::int
		)
		SELECT isin, slot, AVG(slot_volume) AS mean_vol, COALESCE(STDDEV(slot_volume), 0) AS std_vol, COUNT(*) AS samples
		FROM tick_slots
		WHERE slot >= 0
		GROUP BY isin, slot`, sinceDate)
	if err != nil {
		return err
	}
	defer rows.Close()

	loaded := 0
	for rows.Next() {
		var isin string
		var slot, samples int
		var meanVol, stdVol float64
		if err := rows.Scan(&isin, &slot, &meanVol, &stdVol, &samples); err != nil {
			return err
		}
		s, ok := stocks[isin]
		if !ok {
			continue
		}
		if s.VolumeSlot == nil {
			s.VolumeSlot = make(map[int]VolumeSlotBaseline)
		}
		s.VolumeSlot[slot] = VolumeSlotBaseline{
			Mean:    meanVol,
			StdDev:  stdVol,
			Samples: samples,
		}
		loaded++
	}
	log.Printf("[baselines] loadVolumeSlotBaselines: %d slot entries loaded", loaded)
	return rows.Err()
}

// loadAvgDailyVolume computes average daily volume over last 10 trading days.
func loadAvgDailyVolume(ctx context.Context, pool *pgxpool.Pool, stocks map[string]*StockState, sinceDate time.Time) error {
	rows, err := pool.Query(ctx,
		`SELECT isin, AVG(tottrdqty)::bigint AS avg_vol
		 FROM nse_cm_bhavcopy
		 WHERE trade_date >= $1
		 GROUP BY isin`, sinceDate)
	if err != nil {
		return err
	}
	defer rows.Close()

	loaded := 0
	for rows.Next() {
		var isin string
		var avgVol int64
		if err := rows.Scan(&isin, &avgVol); err != nil {
			return err
		}
		if s, ok := stocks[isin]; ok {
			s.AvgDailyVolume = avgVol
			loaded++
		}
	}
	log.Printf("[baselines] loadAvgDailyVolume: %d stocks loaded", loaded)
	return rows.Err()
}

// ---------------------------------------------------------------------------
// Sector Mapping
// ---------------------------------------------------------------------------

// sectorMembers is a static mapping of Nifty sector indices to member ISINs.
// TODO: Replace with DB query from nse_indices_daily or a sector constituents table.
var sectorMembers = map[string][]string{
	"NIFTY_BANK":     {},
	"NIFTY_IT":       {},
	"NIFTY_FMCG":     {},
	"NIFTY_PHARMA":   {},
	"NIFTY_AUTO":     {},
	"NIFTY_METAL":    {},
	"NIFTY_REALTY":   {},
	"NIFTY_ENERGY":   {},
	"NIFTY_INFRA":    {},
	"NIFTY_PSU_BANK": {},
	"NIFTY_FIN_SVC":  {},
	"NIFTY_MEDIA":    {},
}

// loadSectorMapping populates SectorState membership and sets StockState.SectorID.
// Currently uses a static placeholder mapping; will be replaced with DB query.
func loadSectorMapping(stocks map[string]*StockState, sectors map[string]*SectorState) {
	// Initialize sector states from static mapping
	for sectorName, memberISINs := range sectorMembers {
		sec := &SectorState{
			Name:        sectorName,
			MemberISINs: memberISINs,
			TotalStocks: len(memberISINs),
		}
		sectors[sectorName] = sec

		// Set SectorID on each member stock
		for _, isin := range memberISINs {
			if s, ok := stocks[isin]; ok {
				s.SectorID = sectorName
			}
		}
	}
	log.Printf("[baselines] loadSectorMapping: %d sectors initialized (static mapping)", len(sectorMembers))
}

// GetSectorNames returns the list of known sector names from the static mapping.
func GetSectorNames() []string {
	names := make([]string, 0, len(sectorMembers))
	for name := range sectorMembers {
		names = append(names, name)
	}
	return names
}
