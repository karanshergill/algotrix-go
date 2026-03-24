package main

import (
	"bufio"
	_ "bytes"
	"context"
	"fmt"
	"log"
	_ "net/http"
	"os"
	"os/exec"
	"sort"
	"strings"

	"database/sql"
	"encoding/csv"
	"encoding/json"
	"math"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/karanshergill/algotrix-go/data/nse"
	"github.com/karanshergill/algotrix-go/db/conns"
	"github.com/karanshergill/algotrix-go/db/ops"
	"github.com/karanshergill/algotrix-go/features"
	"github.com/karanshergill/algotrix-go/feed"
	"github.com/karanshergill/algotrix-go/internal/auth"
	"github.com/karanshergill/algotrix-go/internal/config"
	"github.com/karanshergill/algotrix-go/models"
	"github.com/karanshergill/algotrix-go/screeners"
	"github.com/karanshergill/algotrix-go/symbols"
	"github.com/karanshergill/algotrix-go/watchlist"
)

func main() {
	cfg, err := config.Load("internal/config/fyers.yaml")
	if err != nil {
		log.Fatal(err)
	}

	// Check for subcommands.
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case "scrips":
			runScrips()
			return
		case "feed":
			runFeed()
			return
		case "ohlcv":
			runOHLCV()
			return
		case "bhavcopy":
			runBhavcopy()
			return
		case "watchlist":
			runWatchlist()
			return
		case "benchmark":
			runBenchmark()
			return
		case "backtest":
			runBacktest()
			return
		case "market-data":
			runMarketData()
			return
		case "universe-refresh":
			runUniverseRefresh()
			return
		}
	}

	a := auth.New(cfg.Fyers)
	if err := a.LoadToken(); err != nil {
		fmt.Println("No valid token. Starting login flow...")
		fmt.Println()
		fmt.Println("Open this URL in your browser:")
		fmt.Println(a.LoginURL())
		fmt.Println()
		fmt.Print("Paste the auth_code from the redirect URL: ")
		reader := bufio.NewReader(os.Stdin)
		code, _ := reader.ReadString('\n')
		code = strings.TrimSpace(code)
		if err := a.Exchange(code); err != nil {
			log.Fatal(err)
		}
		fmt.Println("Token saved.")
	}

	if err := a.Validate(); err != nil {
		log.Fatal("Token invalid: ", err)
	}

	profile, err := a.Model().GetProfile()
	if err != nil {
		log.Fatal("Failed to get profile: ", err)
	}

	fmt.Println()
	fmt.Println("Connected successfully!")
	fmt.Println("Profile:", profile)

	ctx := context.Background()

	dbCfg, err := conns.LoadDBConfig("db/conns/db.yaml")
	if err != nil {
		log.Fatal("Failed to load db config: ", err)
	}

	pgPool, err := conns.NewPostgresPool(ctx, &dbCfg.Postgres)
	if err != nil {
		log.Fatal("Postgres connection failed: ", err)
	}
	defer pgPool.Close()

	if err := symbols.Load(ctx, pgPool); err != nil {
		log.Fatal("Symbol load failed: ", err)
	}

	_ = cfg
}

// runScrips fetches scrip master data from NSE and upserts into nse_cm_scrips.
// It reads the universe from the unified symbols table.
// Usage: go run . scrips --symbol SBIN   (single stock test)
//
//	go run . scrips                  (all active symbols)
func runScrips() {
	ctx := context.Background()

	// Parse --symbol flag.
	var singleSymbol string
	for i, arg := range os.Args {
		if arg == "--symbol" && i+1 < len(os.Args) {
			singleSymbol = os.Args[i+1]
		}
	}

	// DB connection.
	dbCfg, err := conns.LoadDBConfig("db/conns/db.yaml")
	if err != nil {
		log.Fatal("Failed to load db config: ", err)
	}

	pgPool, err := conns.NewPostgresPool(ctx, &dbCfg.Postgres)
	if err != nil {
		log.Fatal("Postgres connection failed: ", err)
	}
	defer pgPool.Close()

	// NSE client.
	nseClient, err := nse.NewClient()
	if err != nil {
		log.Fatal("NSE client failed: ", err)
	}

	// Build symbol list.
	var symbolList []string
	if singleSymbol != "" {
		symbolList = []string{singleSymbol}
	} else {
		symbolList, err = ops.FetchActiveSymbols(ctx, pgPool)
		if err != nil {
			log.Fatal("Failed to fetch symbols: ", err)
		}
	}

	fmt.Printf("Fetching scrip data for %d symbols...\n", len(symbolList))

	success, skipped, failed := 0, 0, 0
	for i, sym := range symbolList {
		scrip := &models.Scrip{}

		// Fetch equity details + trade info.
		if err := nseClient.FetchEquityDetails(sym, scrip); err != nil {
			if strings.Contains(err.Error(), "skipping ETF/MF") {
				fmt.Printf("[%d/%d] SKIP %s: ETF/MF\n", i+1, len(symbolList), sym)
				skipped++
				continue
			}
			fmt.Printf("[%d/%d] FAIL %s: %v\n", i+1, len(symbolList), sym, err)
			failed++
			continue
		}

		// Fetch shareholding + XBRL.
		if err := nseClient.FetchShareholding(sym, scrip); err != nil {
			fmt.Printf("[%d/%d] WARN %s: shareholding failed: %v\n", i+1, len(symbolList), sym, err)
		}

		// Upsert to DB.
		if err := ops.UpsertScrip(ctx, pgPool, scrip); err != nil {
			fmt.Printf("[%d/%d] FAIL %s: db upsert: %v\n", i+1, len(symbolList), sym, err)
			failed++
			continue
		}

		success++
		fmt.Printf("[%d/%d] OK %s — %s (mcap: %d, fii: %.2f%%, dii: %.2f%%)\n",
			i+1, len(symbolList), sym, scrip.CompanyName,
			scrip.MarketCap, scrip.FIIPct, scrip.DIIPct)
	}

	fmt.Printf("\nDone. Success: %d, Skipped: %d, Failed: %d\n", success, skipped, failed)
}

// runFeed starts the live market data feed system.
// Usage: go run . feed [--symbols NSE:RELIANCE-EQ,NSE:HDFCBANK-EQ] --config feed/config.yaml
// If --symbols is omitted, qualified symbols are loaded from DB (WHERE is_tradeable = true).
func runFeed() {
	var symbolsFlag, configPath string
	configPath = "feed/config.yaml" // default

	for i, arg := range os.Args {
		if arg == "--symbols" && i+1 < len(os.Args) {
			symbolsFlag = os.Args[i+1]
		}
		if arg == "--config" && i+1 < len(os.Args) {
			configPath = os.Args[i+1]
		}
	}

	// Auth.
	cfg, err := config.Load("internal/config/fyers.yaml")
	if err != nil {
		log.Fatal(err)
	}
	a := auth.New(cfg.Fyers)
	if err := a.LoadToken(); err != nil {
		log.Fatal("No valid Fyers token. Run auth first.")
	}
	if err := a.Validate(); err != nil {
		log.Fatal("Fyers token invalid: ", err)
	}

	// --- Feature Engine: start BEFORE recorder so we don't miss ticks ---
	feCtx, feCancel := context.WithCancel(context.Background())
	defer feCancel()

	// Load feed config to get DB DSN
	feedCfg, err := feed.LoadConfig(configPath)
	if err != nil {
		log.Fatal("load feed config for feature engine: ", err)
	}

	feEngine, feAdapter, err := features.StartFeatureEngine(feCtx, feedCfg.Feed.Storage.PostgresDSN, nil)
	if err != nil {
		log.Printf("[FeatureEngine] startup failed (non-fatal): %v", err)
	} else {
		log.Printf("[FeatureEngine] LIVE — %d stocks, features at http://127.0.0.1:3003/features", len(feEngine.Stocks()))
	}

	// Resolve symbol list: CLI flag or DB query
	var symbolList []string
	if symbolsFlag != "" {
		symbolList = strings.Split(symbolsFlag, ",")
		for i := range symbolList {
			symbolList[i] = strings.TrimSpace(symbolList[i])
		}
		log.Printf("[Feed] using %d symbols from --symbols flag", len(symbolList))
	} else {
		symbolList, err = loadTradeableSymbols(feedCfg.Feed.Storage.PostgresDSN)
		if err != nil {
			log.Fatalf("[Feed] failed to load symbols from DB: %v", err)
		}
		log.Printf("[Feed] loaded %d tradeable symbols from DB", len(symbolList))
	}

	if len(symbolList) == 0 {
		log.Fatal("[Feed] no symbols to subscribe — check is_tradeable or --symbols flag")
	}

	// --- Screener Engine: wire after feature engine ---
	// Screener uses atdb pool (signals table migrated to atdb)
	atdbPool, poolErr := pgxpool.New(feCtx, feedCfg.Feed.Storage.PostgresDSN)
	if poolErr != nil {
		log.Printf("[Screener] atdb pool failed (non-fatal): %v", poolErr)
	}
	// Signal broadcast function — set after recorder is created
	var broadcastSignal func(map[string]interface{})
	scrEngine, err := screeners.Setup(feCtx, atdbPool)
	if err != nil {
		log.Printf("[Screener] setup failed (non-fatal): %v", err)
	} else {
		log.Println("[Screener] LIVE — 5 screeners active")

		// Wire onTick: feature engine calls screeners after each tick
		feEngine.SetOnTick(func(isin string) {
			snap := feEngine.Snapshot()
			if snap == nil {
				return
			}
			stockSnap, ok := snap.Stocks[isin]
			if !ok {
				return
			}
			signals := scrEngine.ProcessTick(isin, &stockSnap, &snap.Market)
			if broadcastSignal != nil {
				for _, sig := range signals {
					broadcastSignal(map[string]interface{}{
						"screener":      sig.ScreenerName,
						"signal_type":   string(sig.SignalType),
						"symbol":        sig.Symbol,
						"isin":          sig.ISIN,
						"ltp":           sig.LTP,
						"trigger_price": sig.TriggerPrice,
						"triggered_at":  sig.TriggeredAt.Format("2006-01-02T15:04:05-07:00"),
					})
				}
			}
		})
	}

	recorder := feed.NewRecorder(configPath, symbolList)

	// Wire signal broadcasting through Hub WebSocket
	if recorder.Hub() != nil {
		broadcastSignal = recorder.Hub().BroadcastSignal
	}

	// Wire tick callback to feature engine
	if feAdapter != nil {
		recorder.SetOnTick(func(symbol, isin string, ltp float64, volume int64, ts time.Time) {
			feAdapter.AdaptTick(symbol, isin, ltp, volume, ts)
		})
	}

	if err := recorder.Start(a.AccessToken()); err != nil {
		log.Fatal("Feed error: ", err)
	}
}

// loadTradeableSymbols queries fy_symbols from the symbols table where is_tradeable = true.
// Also includes active index fy_symbols.
func loadTradeableSymbols(dsn string) ([]string, error) {
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("connect: %w", err)
	}
	defer pool.Close()

	rows, err := pool.Query(ctx,
		`SELECT fy_symbol FROM symbols WHERE status = 'active' AND is_tradeable = true
		 UNION ALL
		 SELECT fy_symbol FROM indices WHERE is_active = true`)
	if err != nil {
		return nil, fmt.Errorf("query: %w", err)
	}
	defer rows.Close()

	var symbols []string
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return nil, err
		}
		symbols = append(symbols, s)
	}
	return symbols, rows.Err()
}

// runUniverseRefresh recalculates the is_tradeable flag on the symbols table.
// Intended to run daily pre-market via cron (e.g. 8:45 AM IST).
// Usage: ./algotrix universe-refresh
func runUniverseRefresh() {
	dsn := "postgres://me:algotrix@localhost:5432/atdb"
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		log.Fatalf("[universe-refresh] connect: %v", err)
	}
	defer pool.Close()

	// Reset all
	tag, err := pool.Exec(ctx, `UPDATE symbols SET is_tradeable = false WHERE is_tradeable = true`)
	if err != nil {
		log.Fatalf("[universe-refresh] reset: %v", err)
	}
	log.Printf("[universe-refresh] reset %d rows to false", tag.RowsAffected())

	// Set qualified stocks
	tag, err = pool.Exec(ctx, `
		UPDATE symbols s SET is_tradeable = true
		FROM (
		  SELECT DISTINCT isin
		  FROM nse_cm_bhavcopy
		  WHERE date >= CURRENT_DATE - INTERVAL '20 days'
		  GROUP BY isin
		  HAVING
		    MAX(close) >= 100
		    AND AVG(volume) >= 100000
		    AND AVG(traded_value) >= 50000000
		    AND COUNT(DISTINCT date) >= (
		      SELECT COUNT(DISTINCT date) FROM nse_cm_bhavcopy
		      WHERE date >= CURRENT_DATE - INTERVAL '20 days'
		    )
		) q
		WHERE s.isin = q.isin AND s.status = 'active'`)
	if err != nil {
		log.Fatalf("[universe-refresh] update: %v", err)
	}
	log.Printf("[universe-refresh] set %d stocks as tradeable", tag.RowsAffected())
}

// runBhavcopy fetches NSE CM bhavcopy data and stores it in nse_cm_bhavcopy.
// Usage: go run . bhavcopy --date 2026-03-13
//        go run . bhavcopy --from 2026-02-01 --to 2026-03-13
func runBhavcopy() {
	var dateFlag, fromFlag, toFlag string
	for i, arg := range os.Args {
		if arg == "--date" && i+1 < len(os.Args) {
			dateFlag = os.Args[i+1]
		}
		if arg == "--from" && i+1 < len(os.Args) {
			fromFlag = os.Args[i+1]
		}
		if arg == "--to" && i+1 < len(os.Args) {
			toFlag = os.Args[i+1]
		}
	}

	// DB connection via database/sql (StoreBhavcopy uses database/sql).
	dbCfg, err := conns.LoadDBConfig("db/conns/db.yaml")
	if err != nil {
		log.Fatal("Failed to load db config: ", err)
	}

	// Register pgx as database/sql driver.
	_ = stdlib.GetDefaultDriver()
	db, err := sql.Open("pgx", dbCfg.Postgres.DSN())
	if err != nil {
		log.Fatal("DB connection failed: ", err)
	}
	defer db.Close()

	var dates []time.Time

	if dateFlag != "" {
		d, err := time.Parse("2006-01-02", dateFlag)
		if err != nil {
			log.Fatalf("Invalid --date: %v", err)
		}
		dates = append(dates, d)
	} else if fromFlag != "" && toFlag != "" {
		from, err := time.Parse("2006-01-02", fromFlag)
		if err != nil {
			log.Fatalf("Invalid --from: %v", err)
		}
		to, err := time.Parse("2006-01-02", toFlag)
		if err != nil {
			log.Fatalf("Invalid --to: %v", err)
		}
		for d := from; !d.After(to); d = d.AddDate(0, 0, 1) {
			// Skip weekends.
			if d.Weekday() == time.Saturday || d.Weekday() == time.Sunday {
				continue
			}
			dates = append(dates, d)
		}
	} else {
		log.Fatal("Usage: bhavcopy --date 2026-03-13  OR  bhavcopy --from 2026-02-01 --to 2026-03-13")
	}

	fmt.Printf("Fetching bhavcopy for %d date(s)...\n", len(dates))

	totalInserted := int64(0)
	for i, d := range dates {
		rows, err := nse.FetchBhavcopy(d)
		if err != nil {
			fmt.Printf("[%d/%d] %s — SKIP: %v\n", i+1, len(dates), d.Format("2006-01-02"), err)
			continue
		}

		inserted, err := nse.StoreBhavcopy(db, rows)
		if err != nil {
			fmt.Printf("[%d/%d] %s — ERROR storing: %v\n", i+1, len(dates), d.Format("2006-01-02"), err)
			continue
		}

		totalInserted += inserted
		fmt.Printf("[%d/%d] %s — %d rows fetched, %d inserted\n", i+1, len(dates), d.Format("2006-01-02"), len(rows), inserted)
	}

	fmt.Printf("\nDone. Total inserted: %d\n", totalInserted)
}

// runWatchlist builds and explains watchlists.
// Usage: algotrix watchlist build [--lookback 30] [--coverage 1.0] [--madtv-floor 1e9] [--json] [--csv /path] [--fno-only] [--weights JSON]
//        algotrix watchlist explain --symbol RELIANCE [--lookback 30]
func runWatchlist() {
	if len(os.Args) < 3 {
		fmt.Println("Usage:")
		fmt.Println("  watchlist build     [--lookback N] [--madtv-floor N] [--json] [--csv path] [--fno-only] [--weights JSON]")
		fmt.Println("  watchlist explain   --symbol SYMBOL [--lookback N]")
		fmt.Println("  watchlist defaults  (prints default config as JSON)")
		return
	}

	subCmd := os.Args[2]

	// Quick subcommand: defaults — no DB needed.
	// Returns raw slider-scale values (integers) matching frontend model.
	if subCmd == "defaults" {
		cfg := watchlist.DefaultConfig()
		// Convert normalized weights (0.10, 0.18...) to raw slider values (10, 18...)
		// by scaling to sum to 100.
		total := cfg.WeightMADTV + cfg.WeightAmihud + cfg.WeightTradeSize + cfg.WeightATRPct +
			cfg.WeightADRPct + cfg.WeightRangeEff + cfg.WeightParkinson + cfg.WeightMomentum +
			cfg.WeightBeta + cfg.WeightRS + cfg.WeightGap + cfg.WeightVolRatio + cfg.WeightEMASlope
		scale := func(w float64) float64 {
			return math.Round(w / total * 100)
		}
		out := map[string]interface{}{
			"lookback":  cfg.LookbackDays,
			"madtvFloor": cfg.MADTVFloor,
			"fnoOnly":   false,
			"weights": map[string]float64{
				"madtv":     scale(cfg.WeightMADTV),
				"amihud":    scale(cfg.WeightAmihud),
				"tradeSize": scale(cfg.WeightTradeSize),
				"atrPct":    scale(cfg.WeightATRPct),
				"adrPct":    scale(cfg.WeightADRPct),
				"rangeEff":  scale(cfg.WeightRangeEff),
				"parkinson": scale(cfg.WeightParkinson),
				"momentum":  scale(cfg.WeightMomentum),
				"beta":      scale(cfg.WeightBeta),
				"rs":        scale(cfg.WeightRS),
				"gap":       scale(cfg.WeightGap),
				"volRatio":  scale(cfg.WeightVolRatio),
				"emaSlope":  scale(cfg.WeightEMASlope),
			},
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(out)
		return
	}

	// Parse common flags.
	var symbolFlag, csvPath, weightsJSON, filtersJSON string
	lookback := 30
	coverage := 1.0
	madtvFloor := 1e9 // ₹100 Crore
	jsonOutput := false
	fnoOnly := false

	for i, arg := range os.Args {
		switch arg {
		case "--symbol":
			if i+1 < len(os.Args) { symbolFlag = os.Args[i+1] }
		case "--lookback":
			if i+1 < len(os.Args) {
				if v, err := strconv.Atoi(os.Args[i+1]); err == nil { lookback = v }
			}
		case "--coverage":
			if i+1 < len(os.Args) {
				if v, err := strconv.ParseFloat(os.Args[i+1], 64); err == nil { coverage = v }
			}
		case "--madtv-floor":
			if i+1 < len(os.Args) {
				if v, err := strconv.ParseFloat(os.Args[i+1], 64); err == nil { madtvFloor = v }
			}
		case "--csv":
			if i+1 < len(os.Args) { csvPath = os.Args[i+1] }
		case "--json":
			jsonOutput = true
		case "--fno-only":
			fnoOnly = true
		case "--weights":
			if i+1 < len(os.Args) { weightsJSON = os.Args[i+1] }
		case "--filters":
			if i+1 < len(os.Args) { filtersJSON = os.Args[i+1] }
		}
	}

	// DB connection.
	dbCfg, err := conns.LoadDBConfig("db/conns/db.yaml")
	if err != nil {
		log.Fatal("Failed to load db config: ", err)
	}
	_ = stdlib.GetDefaultDriver()
	db, err := sql.Open("pgx", dbCfg.Postgres.DSN())
	if err != nil {
		log.Fatal("DB connection failed: ", err)
	}
	defer db.Close()

	// Build config.
	cfg := watchlist.DefaultConfig()
	cfg.LookbackDays = lookback
	cfg.MinCoverage = coverage
	cfg.MADTVFloor = madtvFloor

	// Parse custom weights JSON: {"madtv":10,"amihud":10,"atrPct":10,...}
	// Values are raw (0-100), auto-normalized to sum to 1.0.
	if weightsJSON != "" {
		var raw map[string]float64
		if err := json.Unmarshal([]byte(weightsJSON), &raw); err == nil {
			var total float64
			for _, v := range raw {
				total += v
			}
			if total > 0 {
				norm := func(key string) float64 {
					if v, ok := raw[key]; ok {
						return v / total
					}
					return 0
				}
				cfg.WeightMADTV = norm("madtv")
				cfg.WeightAmihud = norm("amihud")
				cfg.WeightTradeSize = norm("tradeSize")
				cfg.WeightATRPct = norm("atrPct")
				cfg.WeightADRPct = norm("adrPct")
				cfg.WeightRangeEff = norm("rangeEff")
				cfg.WeightParkinson = norm("parkinson")
				cfg.WeightMomentum = norm("momentum")
			}
		}
	}

	// Parse per-metric filter thresholds JSON.
	if filtersJSON != "" {
		var raw map[string]float64
		if err := json.Unmarshal([]byte(filtersJSON), &raw); err == nil {
			// TODO: if v, ok := raw["minADRPct"]; ok { cfg.MinADRPct = v }
			// TODO: if v, ok := raw["minRangeEff"]; ok { cfg.MinRangeEff = v }
			// TODO: if v, ok := raw["minMomentum"]; ok { cfg.MinMomentum = v }
			// TODO: if v, ok := raw["minParkinson"]; ok { cfg.MinParkinson = v }
			// TODO: if v, ok := raw["maxAmihud"]; ok { cfg.MaxAmihud = v }
			// TODO: if v, ok := raw["minTradeSize"]; ok { cfg.MinTradeSize = v }
			// TODO: // TODO: if v, ok := raw["minATRPct"]; ok { cfg.MinATRPct = v }
			// TODO: // TODO: if v, ok := raw["minBeta"]; ok { cfg.MinBeta = v }
			// TODO: // TODO: if v, ok := raw["minRS"]; ok { cfg.MinRS = v }
			// TODO: if v, ok := raw["minGap"]; ok { cfg.MinGap = v }
			// TODO: if v, ok := raw["minVolRatio"]; ok { cfg.MinVolRatio = v }
			// TODO: if v, ok := raw["minEMASlope"]; ok { cfg.MinEMASlope = v }
		}
	}

	// FnO-only universe filter.
	if fnoOnly {
		fnoISINs, err := fetchFnOISINs(db)
		if err != nil {
			log.Fatal("Failed to fetch FnO ISINs: ", err)
		}
		cfg.UniverseISINs = fnoISINs
		cfg.MADTVFloor = 5e9 // ₹50Cr for FnO
	}

	// Build ISIN→Symbol lookup.
	symbolLookup, err := fetchSymbolLookup(db)
	if err != nil {
		log.Fatal("Failed to build symbol lookup: ", err)
	}

	// Run the builder.
	result, err := watchlist.Build(db, cfg)
	if err != nil {
		log.Fatal("Watchlist build failed: ", err)
	}

	switch subCmd {
	case "build":
		if jsonOutput {
			// Include symbol lookup for qualified ISINs.
			symMap := make(map[string]string, len(result.Qualified))
			for _, s := range result.Qualified {
				if sym := symbolLookup[s.ISIN]; sym != "" {
					symMap[s.ISIN] = sym
				}
			}
			out := struct {
				Qualified []watchlist.StockScore            `json:"Qualified"`
				Rejected  int                               `json:"Rejected"`
				Total     int                               `json:"Total"`
				Symbols   map[string]string                 `json:"Symbols"`
				Stats     map[string]watchlist.MetricStats  `json:"Stats"`
			}{result.Qualified, result.Rejected, result.Total, symMap, result.Stats}
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			enc.Encode(out)
			return
		}

		// Pipeline summary.
		universe := "NSE Equities"
		if fnoOnly { universe = "FnO Only" }
		fmt.Println("╔══════════════════════════════════════════════════════════════╗")
		fmt.Println("║                    WATCHLIST BUILD REPORT                    ║")
		fmt.Println("╚══════════════════════════════════════════════════════════════╝")
		fmt.Println()
		fmt.Printf("  Universe:     %s\n", universe)
		fmt.Printf("  Lookback:     %d trading days\n", lookback)
		fmt.Printf("  Coverage:     %.0f%%\n", coverage*100)
		fmt.Printf("  MADTV Floor:  ₹%.0f Cr\n", madtvFloor/1e7)
		fmt.Println()
		fmt.Println("  PIPELINE:")
		fmt.Printf("    Total evaluated:  %d\n", result.Total)
		fmt.Printf("    Rejected:         %d\n", result.Rejected)
		fmt.Printf("    Qualified:        %d\n", len(result.Qualified))
		fmt.Println()
		fmt.Printf("  WEIGHTS:\n")
		fmt.Printf("    Tradability: MADTV=%.0f%% | Amihud=%.0f%% | TradeSize=%.0f%% | ATR%%=%.0f%%\n",
			cfg.WeightMADTV*100, cfg.WeightAmihud*100, cfg.WeightTradeSize*100, cfg.WeightATRPct*100)
		fmt.Printf("    Opportunity: ADR%%=%.0f%% | RangeEff=%.0f%% | Parkinson=%.0f%% | Momentum=%.0f%%\n",
			cfg.WeightADRPct*100, cfg.WeightRangeEff*100, cfg.WeightParkinson*100, cfg.WeightMomentum*100)
		fmt.Printf("    Market Ctx:  Beta=%.0f%% | RS=%.0f%% | Gap%%=%.0f%% | VolRatio=%.0f%% | EMASlope=%.0f%%\n",
			cfg.WeightBeta*100, cfg.WeightRS*100, cfg.WeightGap*100, cfg.WeightVolRatio*100, cfg.WeightEMASlope*100)
		fmt.Println()

		// Ranked table.
		fmt.Println("  QUALIFIED STOCKS (ranked by composite score):")
		fmt.Println("  ─────────────────────────────────────────────────────────────────────────────────────")
		fmt.Printf("  %-4s %-12s %-15s %7s %7s %7s %7s %7s %7s %7s %7s %7s\n",
			"#", "SYMBOL", "ISIN", "MADTV%", "Amhd%", "ATR%P", "Park%", "TrdSz%", "ADR%P", "RngEf%", "Mom%", "SCORE")
		fmt.Println("  " + strings.Repeat("─", 120))

		for i, s := range result.Qualified {
			sym := symbolLookup[s.ISIN]
			if sym == "" { sym = "???" }
			fmt.Printf("  %-4d %-12s %-15s %6.1f %6.1f %6.1f %6.1f %6.1f %6.1f %6.1f %6.1f %6.1f\n",
				i+1, sym, s.ISIN, s.PctMADTV, s.PctAmihud, s.PctATRPct,
				s.PctParkinson, s.PctTradeSize, s.PctADRPct, s.PctRangeEff, s.PctMomentum, s.Composite)
		}

		// Score distribution.
		fmt.Println()
		fmt.Println("  SCORE DISTRIBUTION:")
		buckets := []struct{ label string; min, max float64 }{
			{"90-100", 90, 100}, {"80-89", 80, 89.99}, {"70-79", 70, 79.99},
			{"60-69", 60, 69.99}, {"50-59", 50, 59.99}, {"40-49", 40, 49.99},
			{"30-39", 30, 39.99}, {"20-29", 20, 29.99}, {"10-19", 10, 19.99}, {"0-9", 0, 9.99},
		}
		for _, b := range buckets {
			count := 0
			for _, s := range result.Qualified {
				if s.Composite >= b.min && s.Composite <= b.max { count++ }
			}
			bar := strings.Repeat("█", count)
			fmt.Printf("    %6s: %3d %s\n", b.label, count, bar)
		}

		// CSV export.
		if csvPath != "" {
			f, err := os.Create(csvPath)
			if err != nil {
				log.Fatalf("Failed to create CSV: %v", err)
			}
			defer f.Close()
			w := csv.NewWriter(f)
			w.Write([]string{"Rank", "Symbol", "ISIN", "MADTV_Raw", "Amihud_Raw", "ATRPct_Raw",
				"Parkinson_Raw", "TradeSize_Raw", "ADRPct_Raw", "RangeEff_Raw", "Momentum5D_Raw", "Days",
				"Pct_MADTV", "Pct_Amihud", "Pct_ATRPct", "Pct_Parkinson", "Pct_TradeSize",
				"Pct_ADRPct", "Pct_RangeEff", "Pct_Momentum", "Composite"})
			for i, s := range result.Qualified {
				sym := symbolLookup[s.ISIN]
				w.Write([]string{
					strconv.Itoa(i + 1), sym, s.ISIN,
					fmt.Sprintf("%.2f", s.MADTV), fmt.Sprintf("%.2e", s.Amihud),
					fmt.Sprintf("%.2f", s.ATRPct), fmt.Sprintf("%.4f", s.Parkinson),
					fmt.Sprintf("%.2f", s.TradeSize), fmt.Sprintf("%.2f", s.ADRPct),
					fmt.Sprintf("%.3f", s.RangeEff), fmt.Sprintf("%.4f", s.Momentum5D),
					strconv.Itoa(s.TradingDays),
					fmt.Sprintf("%.1f", s.PctMADTV), fmt.Sprintf("%.1f", s.PctAmihud),
					fmt.Sprintf("%.1f", s.PctATRPct), fmt.Sprintf("%.1f", s.PctParkinson),
					fmt.Sprintf("%.1f", s.PctTradeSize), fmt.Sprintf("%.1f", s.PctADRPct),
					fmt.Sprintf("%.1f", s.PctRangeEff), fmt.Sprintf("%.1f", s.PctMomentum),
					fmt.Sprintf("%.1f", s.Composite),
				})
			}
			w.Flush()
			fmt.Printf("\n  CSV exported to: %s\n", csvPath)
		}

	case "explain":
		if symbolFlag == "" {
			log.Fatal("--symbol is required for explain. Example: watchlist explain --symbol RELIANCE")
		}

		// Find ISIN for symbol.
		targetISIN := ""
		for isin, sym := range symbolLookup {
			if strings.EqualFold(sym, symbolFlag) {
				targetISIN = isin
				break
			}
		}
		if targetISIN == "" {
			log.Fatalf("Symbol %s not found in database", symbolFlag)
		}

		// Find in qualified list.
		var found *watchlist.StockScore
		rank := 0
		for i, s := range result.Qualified {
			if s.ISIN == targetISIN {
				found = &result.Qualified[i]
				rank = i + 1
				break
			}
		}

		if jsonOutput {
			out := map[string]interface{}{
				"symbol":         symbolFlag,
				"isin":           targetISIN,
				"lookback":       lookback,
				"coverage":       coverage,
				"totalQualified": len(result.Qualified),
			}
			if found == nil {
				out["status"] = "rejected"
				out["rank"] = nil
			} else {
				out["status"] = "qualified"
				out["rank"] = rank
				out["raw"] = map[string]interface{}{
					"madtv":       found.MADTV,
					"amihud":      found.Amihud,
					"atrPct":      found.ATRPct,
					"parkinson":   found.Parkinson,
					"tradeSize":   found.TradeSize,
					"adrPct":      found.ADRPct,
					"rangeEff":    found.RangeEff,
					"momentum5d":  found.Momentum5D,
					"tradingDays": found.TradingDays,
				}
				out["percentiles"] = map[string]interface{}{
					"pctMADTV":     found.PctMADTV,
					"pctAmihud":    found.PctAmihud,
					"pctATRPct":    found.PctATRPct,
					"pctParkinson": found.PctParkinson,
					"pctTradeSize": found.PctTradeSize,
					"pctADRPct":    found.PctADRPct,
					"pctRangeEff":  found.PctRangeEff,
					"pctMomentum":  found.PctMomentum,
				}
				out["composite"] = found.Composite

				type breakdownItem struct {
					Metric     string  `json:"metric"`
					Percentile float64 `json:"percentile"`
					Weight     float64 `json:"weight"`
					Points     float64 `json:"points"`
				}
				out["breakdown"] = []breakdownItem{
					{"MADTV", found.PctMADTV, cfg.WeightMADTV, found.PctMADTV * cfg.WeightMADTV},
					{"Amihud", found.PctAmihud, cfg.WeightAmihud, found.PctAmihud * cfg.WeightAmihud},
					{"ATR%", found.PctATRPct, cfg.WeightATRPct, found.PctATRPct * cfg.WeightATRPct},
					{"Parkinson", found.PctParkinson, cfg.WeightParkinson, found.PctParkinson * cfg.WeightParkinson},
					{"TradeSize", found.PctTradeSize, cfg.WeightTradeSize, found.PctTradeSize * cfg.WeightTradeSize},
					{"ADR%", found.PctADRPct, cfg.WeightADRPct, found.PctADRPct * cfg.WeightADRPct},
					{"RangeEff", found.PctRangeEff, cfg.WeightRangeEff, found.PctRangeEff * cfg.WeightRangeEff},
					{"Momentum", found.PctMomentum, cfg.WeightMomentum, found.PctMomentum * cfg.WeightMomentum},
				}

				type dimInfo struct {
					name string
					pct  float64
				}
				dims := []dimInfo{
					{"MADTV (liquidity quantity)", found.PctMADTV},
					{"Amihud (liquidity quality)", found.PctAmihud},
					{"ATR% (total volatility)", found.PctATRPct},
					{"Parkinson (intraday range)", found.PctParkinson},
					{"Trade Size (institutional)", found.PctTradeSize},
					{"ADR% (daily range)", found.PctADRPct},
					{"Range Efficiency (capturability)", found.PctRangeEff},
					{"Momentum (5D trend)", found.PctMomentum},
				}
				var strengths, weaknesses []string
				for _, d := range dims {
					if d.pct >= 75 {
						strengths = append(strengths, d.name)
					}
					if d.pct < 30 {
						weaknesses = append(weaknesses, d.name)
					}
				}
				out["strengths"] = strengths
				out["weaknesses"] = weaknesses
			}
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			enc.Encode(out)
			return
		}

		fmt.Println("╔══════════════════════════════════════════════════════════════╗")
		fmt.Printf("║           WATCHLIST EXPLAIN: %-12s                    ║\n", symbolFlag)
		fmt.Println("╚══════════════════════════════════════════════════════════════╝")
		fmt.Println()
		fmt.Printf("  Symbol: %s\n", symbolFlag)
		fmt.Printf("  ISIN:   %s\n", targetISIN)
		fmt.Printf("  Lookback: %d days | Coverage: %.0f%%\n", lookback, coverage*100)
		fmt.Println()

		if found == nil {
			fmt.Println("  STATUS: ❌ REJECTED")
			fmt.Println()
			fmt.Println("  This stock did not qualify for the watchlist.")
			fmt.Println("  Possible reasons:")
			fmt.Println("    - MADTV below floor (₹" + fmt.Sprintf("%.0f", madtvFloor/1e7) + " Cr)")
			fmt.Println("    - Insufficient trading day coverage")
			fmt.Println("    - ETF/MF (ISIN starts with INF)")
			fmt.Println("    - Missing metric data")
			return
		}

		fmt.Printf("  STATUS: ✅ QUALIFIED (Rank #%d of %d)\n", rank, len(result.Qualified))
		fmt.Println()

		// Raw metrics.
		fmt.Println("  RAW METRICS:")
		fmt.Printf("    MADTV:          ₹%.2f Cr\n", found.MADTV/1e7)
		fmt.Printf("    Amihud:         %.2e\n", found.Amihud)
		fmt.Printf("    ATR%%:           %.2f%%\n", found.ATRPct)
		fmt.Printf("    Parkinson:      %.2f%% daily\n", found.Parkinson*100)
		fmt.Printf("    Avg Trade Size: ₹%.0f\n", found.TradeSize)
		fmt.Printf("    ADR%%:           %.2f%%\n", found.ADRPct)
		fmt.Printf("    Range Eff:      %.3f\n", found.RangeEff)
		fmt.Printf("    Momentum 5D:    %.2f%%\n", found.Momentum5D*100)
		fmt.Printf("    Trading Days:   %d\n", found.TradingDays)
		fmt.Println()

		// Percentile breakdown with visual bars.
		fmt.Println("  PERCENTILE RANK (vs qualified pool):")
		printBar := func(label string, pct float64, note string) {
			filled := int(math.Round(pct / 5))
			empty := 20 - filled
			bar := strings.Repeat("█", filled) + strings.Repeat("░", empty)
			extra := ""
			if note != "" { extra = " " + note }
			fmt.Printf("    %-14s %5.1f  %s%s\n", label, pct, bar, extra)
		}
		printBar("MADTV", found.PctMADTV, "")
		printBar("Amihud", found.PctAmihud, "(inverted)")
		printBar("ATR%", found.PctATRPct, "")
		printBar("Parkinson", found.PctParkinson, "")
		printBar("Trade Size", found.PctTradeSize, "")
		printBar("ADR%", found.PctADRPct, "")
		printBar("Range Eff", found.PctRangeEff, "")
		printBar("Momentum", found.PctMomentum, "(abs 5D)")
		fmt.Println()

		// Composite score breakdown.
		fmt.Println("  COMPOSITE SCORE BREAKDOWN:")
		type scoreLine struct{ label string; pct, weight float64 }
		scoreLines := []scoreLine{
			{"MADTV", found.PctMADTV, cfg.WeightMADTV},
			{"Amihud", found.PctAmihud, cfg.WeightAmihud},
			{"ATR%", found.PctATRPct, cfg.WeightATRPct},
			{"Parkinson", found.PctParkinson, cfg.WeightParkinson},
			{"Trade Size", found.PctTradeSize, cfg.WeightTradeSize},
			{"ADR%", found.PctADRPct, cfg.WeightADRPct},
			{"Range Eff", found.PctRangeEff, cfg.WeightRangeEff},
			{"Momentum", found.PctMomentum, cfg.WeightMomentum},
		}
		for _, sl := range scoreLines {
			fmt.Printf("    %-12s %5.1f × %.2f = %5.1f pts\n", sl.label+":", sl.pct, sl.weight, sl.pct*sl.weight)
		}
		fmt.Println("    ─────────────────────────────────")
		fmt.Printf("    TOTAL:                    %5.1f / 100\n", found.Composite)
		fmt.Println()

		// Strength/weakness summary.
		type dim struct{ name string; pct float64 }
		dims := []dim{
			{"MADTV (liquidity quantity)", found.PctMADTV},
			{"Amihud (liquidity quality)", found.PctAmihud},
			{"ATR% (total volatility)", found.PctATRPct},
			{"ADR% (daily range)", found.PctADRPct},
			{"Range Eff (capturability)", found.PctRangeEff},
			{"Momentum (5D trend)", found.PctMomentum},
			{"Parkinson (intraday range)", found.PctParkinson},
			{"Trade Size (institutional)", found.PctTradeSize},
		}
		fmt.Println("  STRENGTHS:")
		for _, d := range dims {
			if d.pct >= 75 {
				fmt.Printf("    ✅ %s: %.1f percentile\n", d.name, d.pct)
			}
		}
		fmt.Println("  WEAKNESSES:")
		for _, d := range dims {
			if d.pct < 30 {
				fmt.Printf("    ⚠️  %s: %.1f percentile\n", d.name, d.pct)
			}
		}

	default:
		fmt.Printf("Unknown watchlist subcommand: %s\n", subCmd)
		fmt.Println("Usage: watchlist build | watchlist explain --symbol SYMBOL")
	}
}

// fetchFnOISINs returns a set of ISINs that are FnO eligible.
func fetchFnOISINs(db *sql.DB) (map[string]bool, error) {
	rows, err := db.Query(`SELECT isin FROM symbols WHERE is_fno = true AND status = 'active'`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make(map[string]bool)
	for rows.Next() {
		var isin string
		if err := rows.Scan(&isin); err != nil { return nil, err }
		result[isin] = true
	}
	return result, rows.Err()
}

// runBacktest runs the rolling historical backtest.
// Usage: go run . backtest [--top N] [--step N] [--fwd 1,5]
func runBacktest() {
	cfg := watchlist.DefaultBacktestConfig()
	jsonOutput := false
	var minMcapCr, maxMcapCr float64
	var weightsJSON, filtersJSON string

	for i, arg := range os.Args {
		switch arg {
		case "--top":
			if i+1 < len(os.Args) {
				if v, err := strconv.Atoi(os.Args[i+1]); err == nil { cfg.TopN = v }
			}
		case "--step":
			if i+1 < len(os.Args) {
				if v, err := strconv.Atoi(os.Args[i+1]); err == nil { cfg.StepDays = v }
			}
		case "--min-mcap":
			if i+1 < len(os.Args) {
				if v, err := strconv.ParseFloat(os.Args[i+1], 64); err == nil { minMcapCr = v }
			}
		case "--max-mcap":
			if i+1 < len(os.Args) {
				if v, err := strconv.ParseFloat(os.Args[i+1], 64); err == nil { maxMcapCr = v }
			}
		case "--lookback":
			if i+1 < len(os.Args) {
				if v, err := strconv.Atoi(os.Args[i+1]); err == nil { cfg.BuildConfig.LookbackDays = v }
			}
		case "--madtv-floor":
			if i+1 < len(os.Args) {
				if v, err := strconv.ParseFloat(os.Args[i+1], 64); err == nil { cfg.BuildConfig.MADTVFloor = v }
			}
		case "--min-score":
			if i+1 < len(os.Args) {
				if v, err := strconv.ParseFloat(os.Args[i+1], 64); err == nil { cfg.BuildConfig.MinCompositeScore = v }
			}
		case "--weights":
			if i+1 < len(os.Args) {
				weightsJSON = os.Args[i+1]
			}
		case "--filters":
			if i+1 < len(os.Args) {
				filtersJSON = os.Args[i+1]
			}
		case "--json":
			jsonOutput = true
		}
	}

	// Convert crores to rupees (1 Cr = 1e7).
	cfg.BuildConfig.MinMarketCap = minMcapCr * 1e7
	cfg.BuildConfig.MaxMarketCap = maxMcapCr * 1e7

	// Parse custom weights JSON: {"madtv":10,"amihud":10,"atrPct":10,...}
	// Values are raw (0-100), auto-normalized to sum to 1.0.
	if weightsJSON != "" {
		var raw map[string]float64
		if err := json.Unmarshal([]byte(weightsJSON), &raw); err == nil {
			var total float64
			for _, v := range raw {
				total += v
			}
			if total > 0 {
				norm := func(key string) float64 {
					if v, ok := raw[key]; ok {
						return v / total
					}
					return 0
				}
				cfg.BuildConfig.WeightMADTV = norm("madtv")
				cfg.BuildConfig.WeightAmihud = norm("amihud")
				cfg.BuildConfig.WeightTradeSize = norm("tradeSize")
				cfg.BuildConfig.WeightATRPct = norm("atrPct")
				cfg.BuildConfig.WeightADRPct = norm("adrPct")
				cfg.BuildConfig.WeightRangeEff = norm("rangeEff")
				cfg.BuildConfig.WeightParkinson = norm("parkinson")
				cfg.BuildConfig.WeightMomentum = norm("momentum")
				cfg.BuildConfig.WeightBeta = norm("beta")
				cfg.BuildConfig.WeightRS = norm("rs")
				cfg.BuildConfig.WeightGap = norm("gap")
				cfg.BuildConfig.WeightVolRatio = norm("volRatio")
				cfg.BuildConfig.WeightEMASlope = norm("emaSlope")
			}
		}
	}

	// Parse per-metric filter thresholds JSON.
	if filtersJSON != "" {
		var raw map[string]float64
		if err := json.Unmarshal([]byte(filtersJSON), &raw); err == nil {
// TODO: 			if v, ok := raw["minADRPct"]; ok { cfg.BuildConfig.MinADRPct = v }
// TODO: 			if v, ok := raw["minRangeEff"]; ok { cfg.BuildConfig.MinRangeEff = v }
// TODO: 			if v, ok := raw["minMomentum"]; ok { cfg.BuildConfig.MinMomentum = v }
// TODO: 			if v, ok := raw["minParkinson"]; ok { cfg.BuildConfig.MinParkinson = v }
// TODO: 			if v, ok := raw["maxAmihud"]; ok { cfg.BuildConfig.MaxAmihud = v }
// TODO: 			if v, ok := raw["minTradeSize"]; ok { cfg.BuildConfig.MinTradeSize = v }
			// TODO: if v, ok := raw["minATRPct"]; ok { cfg.BuildConfig.MinATRPct = v }
			// TODO: if v, ok := raw["minBeta"]; ok { cfg.BuildConfig.MinBeta = v }
			// TODO: if v, ok := raw["minRS"]; ok { cfg.BuildConfig.MinRS = v }
// TODO: 			if v, ok := raw["minGap"]; ok { cfg.BuildConfig.MinGap = v }
// TODO: 			if v, ok := raw["minVolRatio"]; ok { cfg.BuildConfig.MinVolRatio = v }
// TODO: 			if v, ok := raw["minEMASlope"]; ok { cfg.BuildConfig.MinEMASlope = v }
		}
	}

	// DB connection.
	dbCfg, err := conns.LoadDBConfig("db/conns/db.yaml")
	if err != nil {
		log.Fatal("Failed to load db config: ", err)
	}
	_ = stdlib.GetDefaultDriver()
	db, err := sql.Open("pgx", dbCfg.Postgres.DSN())
	if err != nil {
		log.Fatal("DB connection failed: ", err)
	}
	defer db.Close()

	symbolLookup, err := fetchSymbolLookup(db)
	if err != nil {
		log.Fatal("Symbol lookup failed: ", err)
	}
	_ = symbolLookup // used in future detailed output

	result, err := watchlist.RunBacktest(db, cfg)
	if err != nil {
		log.Fatalf("Backtest failed: %v", err)
	}

	if jsonOutput {
		type jsonPick struct {
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
		type jsonDateResult struct {
			BuildDate string            `json:"build_date"`
			Horizon   int               `json:"horizon"`
			Metrics   map[string]float64 `json:"metrics"`
			Benchmark map[string]float64 `json:"benchmark"`
			Picks     []jsonPick         `json:"picks"`
		}
		type jsonSummary struct {
			AvgMaxOpp  float64 `json:"avg_max_opp"`
			AvgOCRet   float64 `json:"avg_oc_ret"`
			AvgRange   float64 `json:"avg_range"`
			AvgHitRate float64 `json:"avg_hit_rate"`
			EdgeMaxOpp float64 `json:"edge_max_opp"`
			EdgeRange  float64 `json:"edge_range"`
			WinCount   int     `json:"win_count"`
			TotalCount int     `json:"total_count"`
		}
		type jsonOutput struct {
			Config  map[string]interface{}    `json:"config"`
			Dates   []jsonDateResult          `json:"dates"`
			Summary map[string]jsonSummary    `json:"summary"`
		}

		var dates []jsonDateResult
		for _, dr := range result.DateResults {
			for _, hr := range dr.Horizons {
				picks := make([]jsonPick, len(hr.Picks))
				for i, p := range hr.Picks {
					picks[i] = jsonPick{
						ISIN: p.ISIN, Rank: p.Rank, Score: p.Score,
						Open: p.Open, High: p.High, Low: p.Low, Close: p.Close,
						MaxOpp: p.MaxOpp, OCReturn: p.OCReturn, RangePct: p.RangePct,
					}
				}
				dates = append(dates, jsonDateResult{
					BuildDate: dr.BuildDate,
					Horizon:   hr.ForwardDays,
					Metrics: map[string]float64{
						"max_opp":  hr.AvgMaxOpp,
						"oc_ret":   hr.AvgOCReturn,
						"range":    hr.AvgRange,
						"hit_rate": hr.HitRate,
					},
					Benchmark: map[string]float64{
						"nifty_max_opp": hr.NiftyAvgMaxOpp,
						"nifty_range":   hr.NiftyAvgRange,
					},
					Picks: picks,
				})
			}
		}

		summaryMap := make(map[string]jsonSummary)
		for fwd, s := range result.Summary {
			winCount := 0
			for _, dr := range result.DateResults {
				for _, hr := range dr.Horizons {
					if hr.ForwardDays == fwd && hr.AvgMaxOpp > hr.NiftyAvgMaxOpp {
						winCount++
					}
				}
			}
			summaryMap[fmt.Sprintf("T+%d", fwd)] = jsonSummary{
				AvgMaxOpp:  s.AvgMaxOpp,
				AvgOCRet:   s.AvgOCReturn,
				AvgRange:   s.AvgRange,
				AvgHitRate: s.AvgHitRate,
				EdgeMaxOpp: s.EdgeMaxOpp,
				EdgeRange:  s.EdgeRange,
				WinCount:   winCount,
				TotalCount: s.NumBuildDates,
			}
		}

		out := jsonOutput{
			Config: map[string]interface{}{
				"top_n": cfg.TopN,
				"step":  cfg.StepDays,
			},
			Dates:   dates,
			Summary: summaryMap,
		}
		enc := json.NewEncoder(os.Stdout)
		if err := enc.Encode(out); err != nil {
			log.Fatalf("JSON encode failed: %v", err)
		}
		return
	}

	// Print results.
	fmt.Println()
	fmt.Println("╔══════════════════════════════════════════════════════════════╗")
	fmt.Println("║             ROLLING HISTORICAL BACKTEST                      ║")
	fmt.Println("╚══════════════════════════════════════════════════════════════╝")
	fmt.Println()
	fmt.Printf("  Top-N: %d | Step: %d days | Horizons: %v\n", cfg.TopN, cfg.StepDays, cfg.ForwardDays)
	fmt.Printf("  Build dates tested: %d\n", len(result.DateResults))
	fmt.Println()

	// Per-date results table.
	for _, fwd := range cfg.ForwardDays {
		fmt.Printf("  ═══ T+%d Forward Performance ═══\n", fwd)
		fmt.Printf("  %-12s %7s %7s %7s %7s %7s %7s\n",
			"BUILD DATE", "MaxOpp", "OC Ret", "Range", "Hit%", "NiftyMO", "NiftyRng")
		fmt.Println("  " + strings.Repeat("─", 75))

		for _, dr := range result.DateResults {
			for _, hr := range dr.Horizons {
				if hr.ForwardDays != fwd {
					continue
				}
				fmt.Printf("  %-12s %+6.2f%% %+6.2f%% %6.2f%% %5.0f%% %+6.2f%% %6.2f%%\n",
					dr.BuildDate, hr.AvgMaxOpp, hr.AvgOCReturn, hr.AvgRange,
					hr.HitRate*100, hr.NiftyAvgMaxOpp, hr.NiftyAvgRange)
			}
		}

		// Summary.
		s := result.Summary[fwd]
		if s != nil && s.NumBuildDates > 0 {
			fmt.Println("  " + strings.Repeat("─", 75))
			fmt.Printf("  %-12s %+6.2f%% %+6.2f%% %6.2f%% %5.0f%% %+6.2f%% %6.2f%%\n",
				"AVERAGE", s.AvgMaxOpp, s.AvgOCReturn, s.AvgRange,
				s.AvgHitRate*100, s.NiftyAvgMaxOpp, s.NiftyAvgRange)
			fmt.Println()
			fmt.Printf("  EDGE vs Nifty 50:\n")
			fmt.Printf("    Max Opportunity: %+.2f%% (%s)\n", s.EdgeMaxOpp,
				func() string { if s.EdgeMaxOpp > 0 { return "builder picks better" }; return "nifty was better" }())
			fmt.Printf("    Session Range:   %+.2f%% (%s)\n", s.EdgeRange,
				func() string { if s.EdgeRange > 0 { return "more tradeable range" }; return "less range than nifty" }())
			fmt.Printf("    Avg Hit Rate:    %.0f%% (stocks with >0.5%% upside from open)\n", s.AvgHitRate*100)
		}
		fmt.Println()
	}
}

// fetchSymbolLookup returns a map of ISIN → Symbol for display.
func fetchSymbolLookup(db *sql.DB) (map[string]string, error) {
	rows, err := db.Query(`SELECT isin, symbol FROM symbols WHERE status = 'active'`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make(map[string]string)
	for rows.Next() {
		var isin, sym string
		if err := rows.Scan(&isin, &sym); err != nil { return nil, err }
		result[isin] = sym
	}
	return result, rows.Err()
}

// runBenchmark compares V2 (8-metric legacy) vs V4 (13-metric) watchlist scoring.
// Usage: go run . benchmark [--top N] [--json]
func runBenchmark() {
	var topN int = 50
	jsonOutput := false

	for i, arg := range os.Args {
		if arg == "--top" && i+1 < len(os.Args) {
			if v, err := strconv.Atoi(os.Args[i+1]); err == nil { topN = v }
		}
		if arg == "--json" {
			jsonOutput = true
		}
	}

	// DB connection.
	dbCfg, err := conns.LoadDBConfig("db/conns/db.yaml")
	if err != nil {
		log.Fatal("Failed to load db config: ", err)
	}
	_ = stdlib.GetDefaultDriver()
	db, err := sql.Open("pgx", dbCfg.Postgres.DSN())
	if err != nil {
		log.Fatal("DB connection failed: ", err)
	}
	defer db.Close()

	symbolLookup, err := fetchSymbolLookup(db)
	if err != nil {
		log.Fatal("Symbol lookup failed: ", err)
	}

	// Build with legacy V2 weights.
	fmt.Println("=== Building V2 (Legacy 8-metric) watchlist ===")
	legacyCfg := watchlist.LegacyConfig()
	v2Result, err := watchlist.Build(db, legacyCfg)
	if err != nil {
		log.Fatalf("V2 build failed: %v", err)
	}

	// Build with new V4 weights (13 metrics).
	fmt.Println("\n=== Building V4 (13-metric) watchlist ===")
	v4Cfg := watchlist.DefaultConfig()
	v4Result, err := watchlist.Build(db, v4Cfg)
	if err != nil {
		log.Fatalf("V4 build failed: %v", err)
	}

	// Build rank maps.
	v2Rank := make(map[string]int)
	v2Score := make(map[string]float64)
	for i, s := range v2Result.Qualified {
		v2Rank[s.ISIN] = i + 1
		v2Score[s.ISIN] = s.Composite
	}
	v4Rank := make(map[string]int)
	v4Score := make(map[string]float64)
	v4Data := make(map[string]*watchlist.StockScore)
	for i, s := range v4Result.Qualified {
		v4Rank[s.ISIN] = i + 1
		v4Score[s.ISIN] = s.Composite
		ss := v4Result.Qualified[i]
		v4Data[s.ISIN] = &ss
	}

	if jsonOutput {
		type comparison struct {
			Symbol    string  `json:"symbol"`
			ISIN      string  `json:"isin"`
			V2Rank    int     `json:"v2Rank"`
			V4Rank    int     `json:"v4Rank"`
			RankDelta int     `json:"rankDelta"`
			V2Score   float64 `json:"v2Score"`
			V4Score   float64 `json:"v4Score"`
			TrendState string `json:"trendState,omitempty"`
			RS         float64 `json:"rs,omitempty"`
			Beta       float64 `json:"beta,omitempty"`
		}
		var comparisons []comparison

		// Collect all ISINs in either list.
		allISINs := make(map[string]bool)
		limit2 := topN; if limit2 > len(v2Result.Qualified) { limit2 = len(v2Result.Qualified) }
		for i := 0; i < limit2; i++ { allISINs[v2Result.Qualified[i].ISIN] = true }
		limit4 := topN; if limit4 > len(v4Result.Qualified) { limit4 = len(v4Result.Qualified) }
		for i := 0; i < limit4; i++ { allISINs[v4Result.Qualified[i].ISIN] = true }

		for isin := range allISINs {
			sym := symbolLookup[isin]
			if sym == "" { sym = isin }
			r2 := v2Rank[isin]
			r4 := v4Rank[isin]
			c := comparison{
				Symbol:    sym,
				ISIN:      isin,
				V2Rank:    r2,
				V4Rank:    r4,
				RankDelta: r2 - r4,
				V2Score:   v2Score[isin],
				V4Score:   v4Score[isin],
			}
			if d, ok := v4Data[isin]; ok {
				c.TrendState = d.TrendState
				c.RS = d.RS
				c.Beta = d.Beta
			}
			comparisons = append(comparisons, c)
		}
		sort.Slice(comparisons, func(i, j int) bool { return comparisons[i].V4Rank < comparisons[j].V4Rank })
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(comparisons)
		return
	}

	// Text output.
	fmt.Println()
	fmt.Println("╔══════════════════════════════════════════════════════════════╗")
	fmt.Println("║              V2 vs V4 BENCHMARK COMPARISON                  ║")
	fmt.Println("╚══════════════════════════════════════════════════════════════╝")
	fmt.Println()
	fmt.Printf("  V2 (8 metrics):  %d qualified\n", len(v2Result.Qualified))
	fmt.Printf("  V4 (13 metrics): %d qualified\n", len(v4Result.Qualified))
	fmt.Println()

	// Biggest risers: stocks that rose the most in rank (V4 rank << V2 rank).
	type rankChange struct {
		isin  string
		v2r   int
		v4r   int
		delta int
	}
	var changes []rankChange
	limit4 := topN; if limit4 > len(v4Result.Qualified) { limit4 = len(v4Result.Qualified) }
	for i := 0; i < limit4; i++ {
		isin := v4Result.Qualified[i].ISIN
		r2 := v2Rank[isin]
		r4 := i + 1
		if r2 == 0 { r2 = len(v2Result.Qualified) + 1 } // not in V2
		changes = append(changes, rankChange{isin, r2, r4, r2 - r4})
	}
	sort.Slice(changes, func(i, j int) bool { return changes[i].delta > changes[j].delta })

	fmt.Println("  🚀 BIGGEST RISERS (V4 ranks them HIGHER than V2):")
	fmt.Printf("  %-4s %-12s %6s %6s %7s  %-12s %6s %6s\n", "#", "SYMBOL", "V2", "V4", "DELTA", "TREND", "RS", "BETA")
	fmt.Println("  " + strings.Repeat("─", 80))
	shown := 0
	for _, c := range changes {
		if shown >= 15 { break }
		sym := symbolLookup[c.isin]
		if sym == "" { sym = "???" }
		v2str := fmt.Sprintf("#%d", c.v2r)
		if c.v2r > len(v2Result.Qualified) { v2str = "NEW" }
		d := v4Data[c.isin]
		trend := ""
		var rs, beta float64
		if d != nil {
			trend = d.TrendState
			rs = d.RS
			beta = d.Beta
		}
		fmt.Printf("  %-4d %-12s %6s %6s %+7d  %-12s %+.3f %5.2f\n",
			shown+1, sym, v2str, fmt.Sprintf("#%d", c.v4r), c.delta, trend, rs, beta)
		shown++
	}

	// Biggest fallers.
	sort.Slice(changes, func(i, j int) bool { return changes[i].delta < changes[j].delta })
	fmt.Println()
	fmt.Println("  📉 BIGGEST FALLERS (V4 ranks them LOWER than V2):")
	fmt.Printf("  %-4s %-12s %6s %6s %7s  %-12s %6s %6s\n", "#", "SYMBOL", "V2", "V4", "DELTA", "TREND", "RS", "BETA")
	fmt.Println("  " + strings.Repeat("─", 80))
	shown = 0
	for _, c := range changes {
		if shown >= 15 { break }
		if c.delta >= 0 { break }
		sym := symbolLookup[c.isin]
		if sym == "" { sym = "???" }
		d := v4Data[c.isin]
		trend := ""
		var rs, beta float64
		if d != nil {
			trend = d.TrendState
			rs = d.RS
			beta = d.Beta
		}
		fmt.Printf("  %-4d %-12s %6s %6s %+7d  %-12s %+.3f %5.2f\n",
			shown+1, sym, fmt.Sprintf("#%d", c.v2r), fmt.Sprintf("#%d", c.v4r), c.delta, trend, rs, beta)
		shown++
	}

	// New in V4 (not in V2 top N).
	fmt.Println()
	fmt.Println("  🆕 NEW IN V4 TOP (not in V2 qualified):")
	shown = 0
	for i := 0; i < limit4 && shown < 10; i++ {
		isin := v4Result.Qualified[i].ISIN
		if _, inV2 := v2Rank[isin]; !inV2 {
			sym := symbolLookup[isin]
			if sym == "" { sym = "???" }
			d := v4Data[isin]
			trend := ""
			if d != nil { trend = d.TrendState }
			fmt.Printf("    V4 #%d  %-12s  trend=%s  score=%.1f\n", i+1, sym, trend, v4Score[isin])
			shown++
		}
	}
	if shown == 0 {
		fmt.Println("    (none)")
	}

	// Overlap analysis.
	v2Top := make(map[string]bool)
	v4Top := make(map[string]bool)
	limit := topN
	if limit > len(v2Result.Qualified) { limit = len(v2Result.Qualified) }
	for i := 0; i < limit; i++ { v2Top[v2Result.Qualified[i].ISIN] = true }
	limit = topN
	if limit > len(v4Result.Qualified) { limit = len(v4Result.Qualified) }
	for i := 0; i < limit; i++ { v4Top[v4Result.Qualified[i].ISIN] = true }

	overlap := 0
	for isin := range v4Top {
		if v2Top[isin] { overlap++ }
	}
	fmt.Println()
	fmt.Printf("  OVERLAP: Top %d — %d stocks in common (%.0f%%)\n",
		topN, overlap, float64(overlap)/float64(topN)*100)
	fmt.Printf("  V4-only: %d new stocks | V2-only: %d dropped stocks\n",
		len(v4Top)-overlap, len(v2Top)-overlap)
}

// runMarketData runs the unified NSE market data pipeline.
// Usage:
//
//	algotrix market-data --date 2026-03-19
//	algotrix market-data --from 2026-02-01 --to 2026-03-19
//	algotrix market-data --date 2026-03-19 --feed cm_bhavcopy,indices_daily
func runMarketData() {
	log.Println("[market-data] WIP — pipeline handlers not yet compiled")
}

// runRegimeScoring runs the Python regime scoring CLI after market data pipeline completes.
func runRegimeScoring(dateStr string) {
	classifierDir := os.Getenv("REGIME_CLASSIFIER_DIR")
	if classifierDir == "" {
		// Default relative path from engine directory.
		classifierDir = "../regime-classifier"
	}

	fmt.Printf("\n--- Regime Scoring for %s ---\n", dateStr)
	cmd := exec.Command("python3", "cli.py", "regime", "daily", "--date", dateStr)
	cmd.Dir = classifierDir
	cmd.Env = append(os.Environ(), "PGPASSWORD=algotrix")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Run(); err != nil {
		fmt.Printf("WARNING: Regime scoring failed: %v\n", err)
	} else {
		fmt.Println("Regime scoring complete.")
	}
}

// sendMarketDataAlert posts a pipeline summary to Discord #system channel.
func sendMarketDataAlert(results []interface{}, dates []time.Time, totalDuration time.Duration, allOK bool) {
	// TODO: re-enable when nse pipeline handlers are compiled
}
