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
	loadSectorMapping(ctx, pool, stocks, sectors)

	return nil
}

// queryTradingDates returns the last N trading dates from nse_cm_bhavcopy,
// ordered most recent first.
func queryTradingDates(ctx context.Context, pool *pgxpool.Pool, n int) ([]time.Time, error) {
	rows, err := pool.Query(ctx,
		`SELECT DISTINCT date FROM nse_cm_bhavcopy ORDER BY date DESC LIMIT $1`, n)
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
		`SELECT isin, close FROM nse_cm_bhavcopy WHERE date = $1`, lastDate)
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
		`SELECT isin, date, open, high, low, close, prev_close
		 FROM nse_cm_bhavcopy
		 WHERE date >= $1
		 ORDER BY isin, date`, oldestDate)
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
		var isin string
		var tradeDate time.Time
		var open, high, low, close, prevClose float64
		if err := rows.Scan(&isin, &tradeDate, &open, &high, &low, &close, &prevClose); err != nil {
			return fmt.Errorf("loadATR: %w", err)
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
				timestamp::date AS trade_day,
				FLOOR(EXTRACT(EPOCH FROM (timestamp::time - '09:15:00'::time)) / 300)::int AS slot,
				MAX(volume) - MIN(volume) AS slot_volume
			FROM nse_cm_ticks
			WHERE timestamp >= $1
			  AND timestamp::time >= '09:15:00'
			  AND timestamp::time < '15:30:00'
			GROUP BY isin, timestamp::date, FLOOR(EXTRACT(EPOCH FROM (timestamp::time - '09:15:00'::time)) / 300)::int
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
		`SELECT isin, AVG(volume)::bigint AS avg_vol
		 FROM nse_cm_bhavcopy
		 WHERE date >= $1
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

// sectorSymbols maps Nifty sector index names to constituent trading symbols.
// Used as a static fallback when the index_constituents DB table is empty/missing.
var sectorSymbols = map[string][]string{
	"NIFTY_BANK":     {"HDFCBANK", "ICICIBANK", "KOTAKBANK", "SBIN", "AXISBANK", "INDUSINDBK", "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB", "PNB", "AUBANK", "BANKBARODA"},
	"NIFTY_IT":       {"TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "MPHASIS", "COFORGE", "PERSISTENT", "LTTS"},
	"NIFTY_FMCG":     {"HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "MARICO", "GODREJCP", "COLPAL", "TATACONSUM", "VBL", "UBL", "EMAMILTD", "PGHH", "RADICO", "JYOTHYLAB"},
	"NIFTY_PHARMA":   {"SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP", "LUPIN", "AUROPHARMA", "TORNTPHARM", "ALKEM", "BIOCON", "GLENMARK", "IPCALAB", "NATCOPHARMA", "LAURUSLABS", "ABBOTINDIA", "SYNGENE", "ZYDUSLIFE", "GRANULES", "AJANTPHARM", "GLAND"},
	"NIFTY_AUTO":     {"TATAMOTORS", "M&M", "MARUTI", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO", "ASHOKLEY", "BALKRISIND", "BHARATFORG", "BOSCHLTD", "MRF", "MOTHERSON", "TVSMOTOR", "EXIDEIND", "TIINDIA"},
	"NIFTY_METAL":    {"TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "COALINDIA", "NMDC", "SAIL", "NATIONALUM", "HINDCOPPER", "APLAPOLLO", "JINDALSTEL", "RATNAMANI", "WELCORP", "MOIL", "HINDZINC"},
	"NIFTY_REALTY":   {"DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "PHOENIXLTD", "BRIGADE", "SOBHA", "SUNTECK", "LODHA", "MAHLIFE"},
	"NIFTY_ENERGY":   {"RELIANCE", "NTPC", "POWERGRID", "ONGC", "ADANIGREEN", "TATAPOWER", "BPCL", "IOC", "GAIL", "ADANIENT"},
	"NIFTY_INFRA":    {"LT", "ADANIPORTS", "ULTRACEMCO", "GRASIM", "NTPC", "POWERGRID", "BHARTIARTL", "DLF", "IRB", "SIEMENS", "ABB", "CUMMINSIND", "THERMAX", "KEC", "KALPATPOWR", "ENGINERSIN", "BEL", "NBCC", "IRCON", "RVNL", "NCC", "HCC", "JKCEMENT", "RAMCOCEM", "DALMIACEM", "AMBUJACEM", "ACC", "CONCOR", "ADANIGREEN", "TATAPOWER"},
	"NIFTY_PSU_BANK": {"SBIN", "PNB", "BANKBARODA", "CANBK", "UNIONBANK", "INDIANB", "IDFCFIRSTB", "MAHABANK", "CENTRALBK", "IOB", "UCOBANK", "BANKINDIA"},
	"NIFTY_FIN_SVC":  {"HDFCBANK", "ICICIBANK", "KOTAKBANK", "SBIN", "AXISBANK", "BAJFINANCE", "BAJAJFINSV", "SBILIFE", "HDFCLIFE", "ICICIPRULI", "ICICIGI", "SBICARD", "MUTHOOTFIN", "CHOLAFIN", "SHRIRAMFIN", "PFC", "RECLTD", "MANAPPURAM", "POONAWALLA", "LICHSGFIN"},
	"NIFTY_MEDIA":    {"ZEEL", "SUNTV", "PVRINOX", "NETWORK18", "TV18BRDCST", "DISHTV", "NAZARA", "HATHWAY", "DEN", "SAREGAMA", "TIPSINDLTD", "NAVNETEDUL", "NDTV", "INFIBEAM", "AFFLE"},
}

// loadSectorMapping populates SectorState membership and sets StockState.SectorID.
// Tries the index_constituents DB table first; falls back to static symbol mapping.
func loadSectorMapping(ctx context.Context, pool *pgxpool.Pool, stocks map[string]*StockState, sectors map[string]*SectorState) {
	if pool != nil {
		if n := loadSectorMappingFromDB(ctx, pool, stocks, sectors); n > 0 {
			log.Printf("[baselines] loadSectorMapping: %d sectors from index_constituents table", n)
			return
		}
	}
	loadSectorMappingStatic(stocks, sectors)
}

// loadSectorMappingFromDB reads from the index_constituents table if it exists.
// Returns the number of sectors populated (0 if table missing or empty).
func loadSectorMappingFromDB(ctx context.Context, pool *pgxpool.Pool, stocks map[string]*StockState, sectors map[string]*SectorState) int {
	rows, err := pool.Query(ctx,
		`SELECT index_name, isin FROM index_constituents`)
	if err != nil {
		// Table likely doesn't exist yet — fall through to static
		return 0
	}
	defer rows.Close()

	sectorISINs := make(map[string][]string)
	for rows.Next() {
		var indexName, isin string
		if err := rows.Scan(&indexName, &isin); err != nil {
			return 0
		}
		sectorISINs[indexName] = append(sectorISINs[indexName], isin)
	}
	if rows.Err() != nil || len(sectorISINs) == 0 {
		return 0
	}

	for sectorName, memberISINs := range sectorISINs {
		sec := &SectorState{
			Name:        sectorName,
			MemberISINs: memberISINs,
			TotalStocks: len(memberISINs),
		}
		sectors[sectorName] = sec
		for _, isin := range memberISINs {
			if s, ok := stocks[isin]; ok {
				s.SectorID = sectorName
			}
		}
	}
	return len(sectorISINs)
}

// loadSectorMappingStatic resolves sectorSymbols (trading symbols) to ISINs
// using the registered stocks map, then populates sectors.
func loadSectorMappingStatic(stocks map[string]*StockState, sectors map[string]*SectorState) {
	// Build symbol → ISIN lookup from registered stocks
	symToISIN := make(map[string]string, len(stocks))
	for _, s := range stocks {
		if s.Symbol != "" {
			symToISIN[s.Symbol] = s.ISIN
		}
	}

	for sectorName, symbols := range sectorSymbols {
		var memberISINs []string
		for _, sym := range symbols {
			if isin, ok := symToISIN[sym]; ok {
				memberISINs = append(memberISINs, isin)
			}
		}
		sec := &SectorState{
			Name:        sectorName,
			MemberISINs: memberISINs,
			TotalStocks: len(memberISINs),
		}
		sectors[sectorName] = sec

		for _, isin := range memberISINs {
			if s, ok := stocks[isin]; ok {
				s.SectorID = sectorName
			}
		}
	}
	log.Printf("[baselines] loadSectorMapping: %d sectors initialized (static mapping)", len(sectorSymbols))
}

// GetSectorNames returns the list of known sector names from the static mapping.
func GetSectorNames() []string {
	names := make([]string, 0, len(sectorSymbols))
	for name := range sectorSymbols {
		names = append(names, name)
	}
	return names
}
