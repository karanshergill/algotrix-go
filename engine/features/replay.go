package features

import (
	"context"
	"encoding/csv"
	"fmt"
	"log"
	"os"
	"sort"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

const algotrixDSN = "postgres://me:algotrix@localhost:5432/algotrix"

// ReplayDay creates a fresh FeatureEngine, loads historical ticks for the given
// date, and replays them sequentially. Returns ISIN -> final feature snapshot.
func ReplayDay(ctx context.Context, pool *pgxpool.Pool, date time.Time) (map[string]map[string]float64, error) {
	engine := NewFeatureEngine(DefaultConfig())

	// Register stocks
	registered, err := RegisterStocksFromDB(ctx, pool, engine)
	if err != nil {
		return nil, fmt.Errorf("register stocks: %w", err)
	}
	if registered == 0 {
		return nil, fmt.Errorf("no stocks registered")
	}

	// Register sectors
	for _, name := range GetSectorNames() {
		if _, ok := engine.sectors[name]; !ok {
			engine.RegisterSector(name, nil)
		}
	}

	// Preload baselines
	if err := PreloadBaselines(ctx, pool, engine.stocks, engine.sectors); err != nil {
		log.Printf("[replay] WARNING: baselines failed: %v", err)
	}

	// Load ticks
	ticks, err := loadTicksForDate(ctx, pool, date)
	if err != nil {
		return nil, fmt.Errorf("load ticks: %w", err)
	}
	if len(ticks) == 0 {
		return nil, fmt.Errorf("no ticks found for %s", date.Format("2006-01-02"))
	}
	log.Printf("[replay] %s: %d ticks loaded, replaying...", date.Format("2006-01-02"), len(ticks))

	return replayTicks(engine, ticks), nil
}

// ReplayRange replays multiple days and writes output to a CSV file.
// One row per stock per day, columns = all 17 features + quality flags.
func ReplayRange(ctx context.Context, pool *pgxpool.Pool, startDate, endDate time.Time, outputPath string) error {
	f, err := os.Create(outputPath)
	if err != nil {
		return fmt.Errorf("create output file: %w", err)
	}
	defer f.Close()

	w := csv.NewWriter(f)
	defer w.Flush()

	// Get feature names for CSV header
	reg := NewDefaultRegistry()
	featureNames := reg.FeatureNames()

	header := []string{"date", "isin", "symbol"}
	header = append(header, featureNames...)
	header = append(header, "partial", "baseline_missing")
	if err := w.Write(header); err != nil {
		return fmt.Errorf("write header: %w", err)
	}

	// Iterate day by day
	daysProcessed := 0
	for d := startDate; !d.After(endDate); d = d.AddDate(0, 0, 1) {
		// Skip weekends
		if d.Weekday() == time.Saturday || d.Weekday() == time.Sunday {
			continue
		}

		result, err := ReplayDay(ctx, pool, d)
		if err != nil {
			log.Printf("[replay] %s: skipping — %v", d.Format("2006-01-02"), err)
			continue
		}

		// Sort ISINs for deterministic output
		isins := make([]string, 0, len(result))
		for isin := range result {
			isins = append(isins, isin)
		}
		sort.Strings(isins)

		dateStr := d.Format("2006-01-02")
		for _, isin := range isins {
			features := result[isin]
			if len(features) == 0 {
				continue
			}

			row := []string{dateStr, isin, ""} // symbol not in features map
			for _, name := range featureNames {
				row = append(row, strconv.FormatFloat(features[name], 'f', 6, 64))
			}
			// Quality flags: derive from features being present
			partial := "false"
			baselineMissing := "false"
			if features["change_pct"] == 0 && features["vwap"] == 0 {
				partial = "true"
			}
			if features["volume_spike_z"] == 0 {
				baselineMissing = "true"
			}
			row = append(row, partial, baselineMissing)

			if err := w.Write(row); err != nil {
				return fmt.Errorf("write row: %w", err)
			}
		}

		daysProcessed++
		log.Printf("[replay] %s: %d stocks written", dateStr, len(isins))
	}

	log.Printf("[replay] range complete: %d days processed, output: %s", daysProcessed, outputPath)
	return nil
}

// replayTicks feeds ticks through the engine synchronously and returns
// ISIN -> final feature map. Used internally by ReplayDay and in tests.
func replayTicks(engine *FeatureEngine, ticks []TickEvent) map[string]map[string]float64 {
	if len(ticks) == 0 {
		return nil
	}

	// Start session with the first tick's timestamp
	engine.session.SessionStart(ticks[0].TS)

	// Process each tick synchronously — no goroutine, no channel
	for _, tick := range ticks {
		engine.handleTick(tick)
	}

	// Collect final snapshots
	snap := engine.Snapshot()
	result := make(map[string]map[string]float64, len(snap.Stocks))
	for isin, ss := range snap.Stocks {
		if len(ss.Features) > 0 {
			result[isin] = ss.Features
		}
	}
	return result
}

// ---------------------------------------------------------------------------
// Tick loaders — primary (atdb) and fallback (algotrix)
// ---------------------------------------------------------------------------

// loadTicksForDate tries atdb.nse_cm_ticks first, falls back to algotrix partitions.
func loadTicksForDate(ctx context.Context, pool *pgxpool.Pool, date time.Time) ([]TickEvent, error) {
	ticks, err := loadTicksFromAtdb(ctx, pool, date)
	if err == nil && len(ticks) > 0 {
		log.Printf("[replay] loaded %d ticks from nse_cm_ticks for %s", len(ticks), date.Format("2006-01-02"))
		return ticks, nil
	}

	ticks, err = loadTicksFromAlgotrix(ctx, date)
	if err != nil {
		return nil, fmt.Errorf("algotrix fallback: %w", err)
	}
	log.Printf("[replay] loaded %d ticks from algotrix for %s", len(ticks), date.Format("2006-01-02"))
	return ticks, nil
}

// loadTicksFromAtdb queries nse_cm_ticks in atdb for a given date.
func loadTicksFromAtdb(ctx context.Context, pool *pgxpool.Pool, date time.Time) ([]TickEvent, error) {
	rows, err := pool.Query(ctx,
		`SELECT isin, timestamp, ltp, volume
		 FROM nse_cm_ticks
		 WHERE timestamp::date = $1
		   AND ltp > 0
		 ORDER BY timestamp ASC`, date)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var ticks []TickEvent
	for rows.Next() {
		var ev TickEvent
		if err := rows.Scan(&ev.ISIN, &ev.TS, &ev.LTP, &ev.Volume); err != nil {
			return nil, err
		}
		ticks = append(ticks, ev)
	}
	return ticks, rows.Err()
}

// loadTicksFromAlgotrix queries algotrix tick_data_YYYYMMDD partitions joined
// with scrip_master to resolve security_id -> ISIN.
func loadTicksFromAlgotrix(ctx context.Context, date time.Time) ([]TickEvent, error) {
	pool, err := pgxpool.New(ctx, algotrixDSN)
	if err != nil {
		return nil, fmt.Errorf("connect to algotrix: %w", err)
	}
	defer pool.Close()

	tableName := fmt.Sprintf("tick_data_%s", date.Format("20060102"))

	// Table name is safe: prefix is constant, suffix is date.Format("20060102") = digits only.
	query := fmt.Sprintf(
		`SELECT sm.isin, t.ts, t.ltp, t.volume
		 FROM %s t
		 JOIN scrip_master sm ON sm.security_id = t.security_id
		 WHERE t.ltp > 0
		 ORDER BY t.ts ASC`, tableName)

	rows, err := pool.Query(ctx, query)
	if err != nil {
		return nil, fmt.Errorf("query %s: %w", tableName, err)
	}
	defer rows.Close()

	var ticks []TickEvent
	for rows.Next() {
		var ev TickEvent
		if err := rows.Scan(&ev.ISIN, &ev.TS, &ev.LTP, &ev.Volume); err != nil {
			return nil, err
		}
		ticks = append(ticks, ev)
	}
	return ticks, rows.Err()
}
