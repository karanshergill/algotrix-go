package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/karanshergill/algotrix-go/data/history"
	"github.com/karanshergill/algotrix-go/data/nse"
	"github.com/karanshergill/algotrix-go/db/conns"
	"github.com/karanshergill/algotrix-go/db/ops"
	"github.com/karanshergill/algotrix-go/feed"
	"github.com/karanshergill/algotrix-go/internal/auth"
	"github.com/karanshergill/algotrix-go/internal/config"
	"github.com/karanshergill/algotrix-go/models"
	"github.com/karanshergill/algotrix-go/symbols"
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
		case "ohlcv":
			runOHLCV()
			return
		case "ohlcv5s":
			runOHLCV5s()
			return
		case "ohlcv1m":
			runOHLCV1m()
			return
		case "feed":
			runFeed()
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

	qdbPool, err := conns.NewQuestDBPool(ctx, &dbCfg.QuestDB)
	if err != nil {
		log.Fatal("QuestDB connection failed: ", err)
	}
	defer qdbPool.Close()

	qdbSender, err := conns.NewQuestDBSender(ctx, &dbCfg.QuestDB)
	if err != nil {
		log.Fatal("QuestDB ILP connection failed: ", err)
	}
	defer qdbSender.Close(ctx)

	if err := symbols.Load(ctx, pgPool); err != nil {
		log.Fatal("Symbol load failed: ", err)
	}

	_ = cfg
	_ = qdbPool
	_ = qdbSender
}

// hasFlag checks if a flag is present in os.Args.
func hasFlag(name string) bool {
	for _, arg := range os.Args {
		if arg == name {
			return true
		}
	}
	return false
}

// queryExistingISINs queries QuestDB HTTP API for distinct ISINs already in a table.
func queryExistingISINs(tableName string) (map[string]bool, error) {
	url := fmt.Sprintf("http://localhost:9000/exec?query=SELECT+DISTINCT+isin+FROM+%s", tableName)
	resp, err := http.Get(url)
	if err != nil {
		return nil, fmt.Errorf("query QuestDB: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var result struct {
		Dataset [][]string `json:"dataset"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("parse QuestDB response: %w", err)
	}

	existing := make(map[string]bool, len(result.Dataset))
	for _, row := range result.Dataset {
		if len(row) > 0 {
			existing[row[0]] = true
		}
	}
	return existing, nil
}

// runScrips fetches scrip master data from NSE and upserts into nse_cm_scrips.
// Usage: go run . scrips --symbol SBIN   (single stock test)
//        go run . scrips                  (all symbols from nse_cm_symbols)
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
		symbolList, err = ops.CategorizeAndFetchSymbols(ctx, pgPool)
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
				_ = ops.InsertSkip(ctx, pgPool, sym, "", "etf", err.Error())
				fmt.Printf("[%d/%d] SKIP %s: ETF/MF\n", i+1, len(symbolList), sym)
				skipped++
				continue
			}
			_ = ops.InsertSkip(ctx, pgPool, sym, "", "api_error", err.Error())
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

// runOHLCV fetches daily OHLCV from Fyers and writes to QuestDB.
// Usage: go run . ohlcv                          (all scrips, default 366 days)
//        go run . ohlcv --symbol SBIN             (single stock test)
//        go run . ohlcv --days 30                 (last 30 days, all scrips)
//        go run . ohlcv --symbol SBIN --days 10   (single stock, 10 days)
func runOHLCV() {
	ctx := context.Background()

	// Parse flags.
	var singleSymbol string
	days := 366 // default: max 1 year for daily resolution
	missingOnly := hasFlag("--missing-only")
	failedRetry := hasFlag("--failed-retry")
	for i, arg := range os.Args {
		if arg == "--symbol" && i+1 < len(os.Args) {
			singleSymbol = os.Args[i+1]
		}
		if arg == "--days" && i+1 < len(os.Args) {
			d, err := strconv.Atoi(os.Args[i+1])
			if err != nil {
				log.Fatalf("Invalid --days value: %s", os.Args[i+1])
			}
			if d < 1 || d > 366 {
				log.Fatal("--days must be between 1 and 366 for daily resolution")
			}
			days = d
		}
	}

	// Fyers auth.
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
	authToken := a.AccessToken()

	// DB conns.
	dbCfg, err := conns.LoadDBConfig("db/conns/db.yaml")
	if err != nil {
		log.Fatal(err)
	}
	pgPool, err := conns.NewPostgresPool(ctx, &dbCfg.Postgres)
	if err != nil {
		log.Fatal(err)
	}
	defer pgPool.Close()

	qdbSender, err := conns.NewQuestDBSender(ctx, &dbCfg.QuestDB)
	if err != nil {
		log.Fatal(err)
	}
	defer qdbSender.Close(ctx)

	// Date range based on --days flag.
	now := time.Now()
	dateTo := now
	dateFrom := now.AddDate(0, 0, -days)

	// Build symbol list: need Fyers symbol + ISIN pairs.
	type symPair struct {
		fySymbol string
		isin     string
	}
	var pairs []symPair

	if singleSymbol != "" {
		// Look up ISIN from nse_cm_symbols.
		var isin string
		err := pgPool.QueryRow(ctx,
			"SELECT isin FROM nse_cm_symbols WHERE symbol = 'NSE:' || $1 || '-EQ'",
			singleSymbol).Scan(&isin)
		if err != nil {
			log.Fatalf("Symbol %s not found: %v", singleSymbol, err)
		}
		pairs = []symPair{{fySymbol: "NSE:" + singleSymbol + "-EQ", isin: isin}}
	} else {
		// All scrips that have been loaded.
		rows, err := pgPool.Query(ctx, `
			SELECT s.symbol, sc.isin
			FROM nse_cm_symbols s
			JOIN nse_cm_scrips sc ON s.isin = sc.isin
			WHERE s.symbol LIKE '%-EQ'
			ORDER BY s.symbol
		`)
		if err != nil {
			log.Fatal(err)
		}
		defer rows.Close()
		for rows.Next() {
			var p symPair
			if err := rows.Scan(&p.fySymbol, &p.isin); err != nil {
				log.Fatal(err)
			}
			pairs = append(pairs, p)
		}
	}

	// Filter to missing-only if requested.
	if missingOnly {
		totalPairs := len(pairs)
		existing, err := queryExistingISINs("nse_cm_ohlcv_1d")
		if err != nil {
			log.Printf("WARN: could not query existing ISINs: %v (proceeding with all)", err)
		} else {
			var filtered []symPair
			for _, p := range pairs {
				if !existing[p.isin] {
					filtered = append(filtered, p)
				}
			}
			pairs = filtered
			fmt.Printf("Filtering to missing-only: %d of %d stocks need backfill\n", len(pairs), totalPairs)
		}
	}

	fmt.Printf("Fetching daily OHLCV for %d symbols (%d days)...\n", len(pairs), days)

	success, failed := 0, 0
	var failedPairs []symPair
	for i, p := range pairs {
		if i > 0 {
			time.Sleep(350 * time.Millisecond) // Fyers rate limit: 10/sec, 200/min
		}
		candles, err := history.FetchDailyOHLCV(authToken, p.fySymbol, p.isin, dateFrom, dateTo)
		if err != nil {
			fmt.Printf("[%d/%d] FAIL %s: %v\n", i+1, len(pairs), p.fySymbol, err)
			failed++
			failedPairs = append(failedPairs, p)
			continue
		}

		if err := ops.WriteOHLCV(ctx, qdbSender, candles); err != nil {
			fmt.Printf("[%d/%d] FAIL %s: write: %v\n", i+1, len(pairs), p.fySymbol, err)
			failed++
			failedPairs = append(failedPairs, p)
			continue
		}

		success++
		fmt.Printf("[%d/%d] OK %s — %d candles\n", i+1, len(pairs), p.fySymbol, len(candles))
	}

	// Retry failed symbols if --failed-retry is set.
	if failedRetry && len(failedPairs) > 0 {
		fmt.Printf("\nRetrying %d failed symbols...\n", len(failedPairs))
		for i, p := range failedPairs {
			if i > 0 {
				time.Sleep(350 * time.Millisecond)
			}
			candles, err := history.FetchDailyOHLCV(authToken, p.fySymbol, p.isin, dateFrom, dateTo)
			if err != nil {
				fmt.Printf("[retry %d/%d] FAIL %s: %v\n", i+1, len(failedPairs), p.fySymbol, err)
				continue
			}
			if err := ops.WriteOHLCV(ctx, qdbSender, candles); err != nil {
				fmt.Printf("[retry %d/%d] FAIL %s: write: %v\n", i+1, len(failedPairs), p.fySymbol, err)
				continue
			}
			fmt.Printf("[retry %d/%d] OK %s — %d candles\n", i+1, len(failedPairs), p.fySymbol, len(candles))
			success++
			failed--
		}
	}

	fmt.Printf("\nDone. Success: %d, Failed after retry: %d\n", success, failed)
}

// runOHLCV5s fetches 5-second OHLCV from Fyers and writes to QuestDB.
// Usage: go run . ohlcv5s                          (all scrips, default 1 day)
//        go run . ohlcv5s --symbol SBIN             (single stock test)
//        go run . ohlcv5s --days 30                 (last 30 days, all scrips — max for 5s)
//        go run . ohlcv5s --symbol SBIN --days 5    (single stock, 5 days)
func runOHLCV5s() {
	ctx := context.Background()

	// Parse flags.
	var singleSymbol string
	days := 1 // default: last 1 day for incremental refresh
	missingOnly := hasFlag("--missing-only")
	failedRetry := hasFlag("--failed-retry")
	for i, arg := range os.Args {
		if arg == "--symbol" && i+1 < len(os.Args) {
			singleSymbol = os.Args[i+1]
		}
		if arg == "--days" && i+1 < len(os.Args) {
			d, err := strconv.Atoi(os.Args[i+1])
			if err != nil {
				log.Fatalf("Invalid --days value: %s", os.Args[i+1])
			}
			if d < 1 || d > 30 {
				log.Fatal("--days must be between 1 and 30 for 5s resolution")
			}
			days = d
		}
	}

	// Fyers auth.
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
	authToken := a.AccessToken()

	// DB conns.
	dbCfg, err := conns.LoadDBConfig("db/conns/db.yaml")
	if err != nil {
		log.Fatal(err)
	}
	pgPool, err := conns.NewPostgresPool(ctx, &dbCfg.Postgres)
	if err != nil {
		log.Fatal(err)
	}
	defer pgPool.Close()

	qdbSender, err := conns.NewQuestDBSender(ctx, &dbCfg.QuestDB)
	if err != nil {
		log.Fatal(err)
	}
	defer qdbSender.Close(ctx)

	// Date range based on --days flag.
	now := time.Now()
	dateTo := now
	dateFrom := now.AddDate(0, 0, -days)

	// Build symbol list.
	type symPair struct {
		fySymbol string
		isin     string
	}
	var pairs []symPair

	if singleSymbol != "" {
		var isin string
		err := pgPool.QueryRow(ctx,
			"SELECT isin FROM nse_cm_symbols WHERE symbol = 'NSE:' || $1 || '-EQ'",
			singleSymbol).Scan(&isin)
		if err != nil {
			log.Fatalf("Symbol %s not found: %v", singleSymbol, err)
		}
		pairs = []symPair{{fySymbol: "NSE:" + singleSymbol + "-EQ", isin: isin}}
	} else {
		rows, err := pgPool.Query(ctx, `
			SELECT s.symbol, sc.isin
			FROM nse_cm_symbols s
			JOIN nse_cm_scrips sc ON s.isin = sc.isin
			WHERE s.symbol LIKE '%-EQ'
			ORDER BY s.symbol
		`)
		if err != nil {
			log.Fatal(err)
		}
		defer rows.Close()
		for rows.Next() {
			var p symPair
			if err := rows.Scan(&p.fySymbol, &p.isin); err != nil {
				log.Fatal(err)
			}
			pairs = append(pairs, p)
		}
	}

	// Filter to missing-only if requested.
	if missingOnly {
		totalPairs := len(pairs)
		existing, err := queryExistingISINs("nse_cm_ohlcv_5s")
		if err != nil {
			log.Printf("WARN: could not query existing ISINs: %v (proceeding with all)", err)
		} else {
			var filtered []symPair
			for _, p := range pairs {
				if !existing[p.isin] {
					filtered = append(filtered, p)
				}
			}
			pairs = filtered
			fmt.Printf("Filtering to missing-only: %d of %d stocks need backfill\n", len(pairs), totalPairs)
		}
	}

	// Split date range into 5-day chunks to keep memory usage low.
	const chunkDays = 5
	var chunks [][2]time.Time
	chunkStart := dateFrom
	for chunkStart.Before(dateTo) {
		chunkEnd := chunkStart.AddDate(0, 0, chunkDays)
		if chunkEnd.After(dateTo) {
			chunkEnd = dateTo
		}
		chunks = append(chunks, [2]time.Time{chunkStart, chunkEnd})
		chunkStart = chunkEnd
	}

	fmt.Printf("Fetching 5s OHLCV for %d symbols (%d days, %d chunks)...\n", len(pairs), days, len(chunks))

	// fetch5sSymbol processes a single symbol across all chunks.
	fetch5sSymbol := func(idx int, p symPair, total int) bool {
		totalCandles := 0
		for _, chunk := range chunks {
			if idx > 0 || chunk != chunks[0] {
				time.Sleep(350 * time.Millisecond) // Fyers rate limit: 10/sec, 200/min
			}
			candles, err := history.Fetch5sOHLCV(authToken, p.fySymbol, p.isin, chunk[0], chunk[1])
			if err != nil {
				fmt.Printf("[%d/%d] FAIL %s: %v\n", idx+1, total, p.fySymbol, err)
				return false
			}
			if len(candles) > 0 {
				if err := ops.Write5sOHLCV(ctx, qdbSender, candles); err != nil {
					fmt.Printf("[%d/%d] FAIL %s: write: %v\n", idx+1, total, p.fySymbol, err)
					return false
				}
				totalCandles += len(candles)
			}
		}
		fmt.Printf("[%d/%d] OK %s — %d candles\n", idx+1, total, p.fySymbol, totalCandles)
		return true
	}

	success, failed := 0, 0
	var failedPairs []symPair
	for i, p := range pairs {
		if fetch5sSymbol(i, p, len(pairs)) {
			success++
		} else {
			failed++
			failedPairs = append(failedPairs, p)
		}
		runtime.GC() // free memory between symbols to prevent OOM
	}

	// Retry failed symbols if --failed-retry is set.
	if failedRetry && len(failedPairs) > 0 {
		fmt.Printf("\nRetrying %d failed symbols...\n", len(failedPairs))
		for i, p := range failedPairs {
			if fetch5sSymbol(i, p, len(failedPairs)) {
				success++
				failed--
			}
			runtime.GC()
		}
	}

	fmt.Printf("\nDone. Success: %d, Failed after retry: %d\n", success, failed)
}

// runOHLCV1m fetches 1-minute OHLCV from Fyers and writes to QuestDB.
// Usage: go run . ohlcv1m                          (all scrips, default 1 day)
//        go run . ohlcv1m --symbol SBIN             (single stock test)
//        go run . ohlcv1m --days 100                (last 100 days, all scrips — max for 1m)
//        go run . ohlcv1m --symbol SBIN --days 5    (single stock, 5 days)
func runOHLCV1m() {
	ctx := context.Background()

	// Parse flags.
	var singleSymbol string
	days := 1 // default: last 1 day for incremental refresh
	missingOnly := hasFlag("--missing-only")
	failedRetry := hasFlag("--failed-retry")
	for i, arg := range os.Args {
		if arg == "--symbol" && i+1 < len(os.Args) {
			singleSymbol = os.Args[i+1]
		}
		if arg == "--days" && i+1 < len(os.Args) {
			d, err := strconv.Atoi(os.Args[i+1])
			if err != nil {
				log.Fatalf("Invalid --days value: %s", os.Args[i+1])
			}
			if d < 1 || d > 100 {
				log.Fatal("--days must be between 1 and 100 for 1m resolution")
			}
			days = d
		}
	}

	// Fyers auth.
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
	authToken := a.AccessToken()

	// DB conns.
	dbCfg, err := conns.LoadDBConfig("db/conns/db.yaml")
	if err != nil {
		log.Fatal(err)
	}
	pgPool, err := conns.NewPostgresPool(ctx, &dbCfg.Postgres)
	if err != nil {
		log.Fatal(err)
	}
	defer pgPool.Close()

	qdbSender, err := conns.NewQuestDBSender(ctx, &dbCfg.QuestDB)
	if err != nil {
		log.Fatal(err)
	}
	defer qdbSender.Close(ctx)

	// Date range based on --days flag.
	now := time.Now()
	dateTo := now
	dateFrom := now.AddDate(0, 0, -days)

	// Build symbol list.
	type symPair struct {
		fySymbol string
		isin     string
	}
	var pairs []symPair

	if singleSymbol != "" {
		var isin string
		err := pgPool.QueryRow(ctx,
			"SELECT isin FROM nse_cm_symbols WHERE symbol = 'NSE:' || $1 || '-EQ'",
			singleSymbol).Scan(&isin)
		if err != nil {
			log.Fatalf("Symbol %s not found: %v", singleSymbol, err)
		}
		pairs = []symPair{{fySymbol: "NSE:" + singleSymbol + "-EQ", isin: isin}}
	} else {
		rows, err := pgPool.Query(ctx, `
			SELECT s.symbol, sc.isin
			FROM nse_cm_symbols s
			JOIN nse_cm_scrips sc ON s.isin = sc.isin
			WHERE s.symbol LIKE '%-EQ'
			ORDER BY s.symbol
		`)
		if err != nil {
			log.Fatal(err)
		}
		defer rows.Close()
		for rows.Next() {
			var p symPair
			if err := rows.Scan(&p.fySymbol, &p.isin); err != nil {
				log.Fatal(err)
			}
			pairs = append(pairs, p)
		}
	}

	// Filter to missing-only if requested.
	if missingOnly {
		totalPairs := len(pairs)
		existing, err := queryExistingISINs("nse_cm_ohlcv_1m")
		if err != nil {
			log.Printf("WARN: could not query existing ISINs: %v (proceeding with all)", err)
		} else {
			var filtered []symPair
			for _, p := range pairs {
				if !existing[p.isin] {
					filtered = append(filtered, p)
				}
			}
			pairs = filtered
			fmt.Printf("Filtering to missing-only: %d of %d stocks need backfill\n", len(pairs), totalPairs)
		}
	}

	fmt.Printf("Fetching 1m OHLCV for %d symbols (%d days)...\n", len(pairs), days)

	success, failed := 0, 0
	var failedPairs []symPair
	for i, p := range pairs {
		if i > 0 {
			time.Sleep(350 * time.Millisecond) // Fyers rate limit: 10/sec, 200/min
		}
		candles, err := history.Fetch1mOHLCV(authToken, p.fySymbol, p.isin, dateFrom, dateTo)
		if err != nil {
			fmt.Printf("[%d/%d] FAIL %s: %v\n", i+1, len(pairs), p.fySymbol, err)
			failed++
			failedPairs = append(failedPairs, p)
			continue
		}

		if err := ops.Write1mOHLCV(ctx, qdbSender, candles); err != nil {
			fmt.Printf("[%d/%d] FAIL %s: write: %v\n", i+1, len(pairs), p.fySymbol, err)
			failed++
			failedPairs = append(failedPairs, p)
			continue
		}

		success++
		fmt.Printf("[%d/%d] OK %s — %d candles\n", i+1, len(pairs), p.fySymbol, len(candles))
	}

	// Retry failed symbols if --failed-retry is set.
	if failedRetry && len(failedPairs) > 0 {
		fmt.Printf("\nRetrying %d failed symbols...\n", len(failedPairs))
		for i, p := range failedPairs {
			if i > 0 {
				time.Sleep(350 * time.Millisecond)
			}
			candles, err := history.Fetch1mOHLCV(authToken, p.fySymbol, p.isin, dateFrom, dateTo)
			if err != nil {
				fmt.Printf("[retry %d/%d] FAIL %s: %v\n", i+1, len(failedPairs), p.fySymbol, err)
				continue
			}
			if err := ops.Write1mOHLCV(ctx, qdbSender, candles); err != nil {
				fmt.Printf("[retry %d/%d] FAIL %s: write: %v\n", i+1, len(failedPairs), p.fySymbol, err)
				continue
			}
			fmt.Printf("[retry %d/%d] OK %s — %d candles\n", i+1, len(failedPairs), p.fySymbol, len(candles))
			success++
			failed--
		}
	}

	fmt.Printf("\nDone. Success: %d, Failed after retry: %d\n", success, failed)
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
