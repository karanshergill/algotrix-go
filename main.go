package main

import (
	"bufio"
	"context"
	"fmt"
	"log"
	"os"
	"strings"

	"github.com/karanshergill/algotrix-go/data/nse"
	"github.com/karanshergill/algotrix-go/database/connections"
	"github.com/karanshergill/algotrix-go/database/operations"
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
	if len(os.Args) > 1 && os.Args[1] == "scrips" {
		runScrips()
		return
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

	dbCfg, err := connections.LoadDBConfig("database/connections/db.yaml")
	if err != nil {
		log.Fatal("Failed to load db config: ", err)
	}

	pgPool, err := connections.NewPostgresPool(ctx, &dbCfg.Postgres)
	if err != nil {
		log.Fatal("Postgres connection failed: ", err)
	}
	defer pgPool.Close()

	qdbPool, err := connections.NewQuestDBPool(ctx, &dbCfg.QuestDB)
	if err != nil {
		log.Fatal("QuestDB connection failed: ", err)
	}
	defer qdbPool.Close()

	qdbSender, err := connections.NewQuestDBSender(ctx, &dbCfg.QuestDB)
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
	dbCfg, err := connections.LoadDBConfig("database/connections/db.yaml")
	if err != nil {
		log.Fatal("Failed to load db config: ", err)
	}

	pgPool, err := connections.NewPostgresPool(ctx, &dbCfg.Postgres)
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
		symbolList, err = operations.CategorizeAndFetchSymbols(ctx, pgPool)
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
				_ = operations.InsertSkip(ctx, pgPool, sym, "", "etf", err.Error())
				fmt.Printf("[%d/%d] SKIP %s: ETF/MF\n", i+1, len(symbolList), sym)
				skipped++
				continue
			}
			_ = operations.InsertSkip(ctx, pgPool, sym, "", "api_error", err.Error())
			fmt.Printf("[%d/%d] FAIL %s: %v\n", i+1, len(symbolList), sym, err)
			failed++
			continue
		}

		// Fetch shareholding + XBRL.
		if err := nseClient.FetchShareholding(sym, scrip); err != nil {
			fmt.Printf("[%d/%d] WARN %s: shareholding failed: %v\n", i+1, len(symbolList), sym, err)
		}

		// Upsert to DB.
		if err := operations.UpsertScrip(ctx, pgPool, scrip); err != nil {
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
