package main

import (
	"bufio"
	"context"
	"fmt"
	"log"
	"os"
	"strings"

	"database/sql"
	"encoding/csv"
	"encoding/json"
	"math"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/stdlib"
	"github.com/karanshergill/algotrix-go/data/nse"
	"github.com/karanshergill/algotrix-go/db/conns"
	"github.com/karanshergill/algotrix-go/db/ops"
	"github.com/karanshergill/algotrix-go/feed"
	"github.com/karanshergill/algotrix-go/internal/auth"
	"github.com/karanshergill/algotrix-go/internal/config"
	"github.com/karanshergill/algotrix-go/models"
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
// Usage: go run . feed --symbols NSE:RELIANCE-EQ,NSE:HDFCBANK-EQ --config feed/config.yaml
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

	if symbolsFlag == "" {
		log.Fatal("--symbols is required. Example: --symbols NSE:RELIANCE-EQ,NSE:HDFCBANK-EQ")
	}

	symbolList := strings.Split(symbolsFlag, ",")
	for i := range symbolList {
		symbolList[i] = strings.TrimSpace(symbolList[i])
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

	recorder := feed.NewRecorder(configPath, symbolList)
	if err := recorder.Start(a.AccessToken()); err != nil {
		log.Fatal("Feed error: ", err)
	}
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
			cfg.WeightADRPct + cfg.WeightRangeEff + cfg.WeightParkinson + cfg.WeightMomentum
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
			},
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(out)
		return
	}

	// Parse common flags.
	var symbolFlag, csvPath, weightsJSON string
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
				Qualified []watchlist.StockScore `json:"Qualified"`
				Rejected  int                    `json:"Rejected"`
				Total     int                    `json:"Total"`
				Symbols   map[string]string      `json:"Symbols"`
			}{result.Qualified, result.Rejected, result.Total, symMap}
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
		fmt.Printf("  WEIGHTS: MADTV=%.0f%% | Amihud=%.0f%% | ATR%%=%.0f%% | Parkinson=%.0f%% | TradeSize=%.0f%% | ADR%%=%.0f%% | RangeEff=%.0f%% | Momentum=%.0f%%\n",
			cfg.WeightMADTV*100, cfg.WeightAmihud*100, cfg.WeightATRPct*100,
			cfg.WeightParkinson*100, cfg.WeightTradeSize*100,
			cfg.WeightADRPct*100, cfg.WeightRangeEff*100, cfg.WeightMomentum*100)
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
