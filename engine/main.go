package main

import (
	"bufio"
	"context"
	"fmt"
	"log"
	"os"
	"strings"

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
		case "feed":
			runFeed()
			return
		case "ohlcv":
			runOHLCV()
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
