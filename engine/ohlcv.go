package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/karanshergill/algotrix-go/data/history"
	"github.com/karanshergill/algotrix-go/db/conns"
	"github.com/karanshergill/algotrix-go/db/ops"
	"github.com/karanshergill/algotrix-go/internal/auth"
	"github.com/karanshergill/algotrix-go/internal/config"
	"github.com/karanshergill/algotrix-go/models"
)

const (
	ohlcvDateLayout = "2006-01-02"
	ohlcvWorkers    = 5
)

type ohlcvResolutionSpec struct {
	Table   string
	MaxDays int
	Fetch   func(authToken, fySymbol, isin string, from, to time.Time) ([]models.OHLCV, error)
}

type ohlcvDateChunk struct {
	From time.Time
	To   time.Time
}

type ohlcvProgress struct {
	Done   int64 `json:"done"`
	Total  int64 `json:"total"`
	Errors int64 `json:"errors"`
}

var ohlcvResolutions = map[string]ohlcvResolutionSpec{
	"1d": {
		Table:   "nse_cm_ohlcv_1d",
		MaxDays: 366,
		Fetch:   history.FetchDailyOHLCV,
	},
	"1m": {
		Table:   "nse_cm_ohlcv_1m",
		MaxDays: 100,
		Fetch:   history.Fetch1mOHLCV,
	},
	"5s": {
		Table:   "nse_cm_ohlcv_5s",
		MaxDays: 30,
		Fetch:   history.Fetch5sOHLCV,
	},
}

func runOHLCV() {
	log.SetOutput(os.Stderr)

	flagSet := flag.NewFlagSet("ohlcv", flag.ExitOnError)
	resolution := flagSet.String("resolution", "", "OHLCV resolution: 1d, 1m, or 5s")
	fromStr := flagSet.String("from", "", "Start date in YYYY-MM-DD")
	toStr := flagSet.String("to", "", "End date in YYYY-MM-DD")
	if err := flagSet.Parse(os.Args[2:]); err != nil {
		log.Fatal(err)
	}

	spec, ok := ohlcvResolutions[*resolution]
	if !ok {
		log.Fatalf("invalid --resolution %q (expected one of: 1d, 1m, 5s)", *resolution)
	}

	from, err := parseOHLCVDate(*fromStr, "--from")
	if err != nil {
		log.Fatal(err)
	}

	to, err := parseOHLCVDate(*toStr, "--to")
	if err != nil {
		log.Fatal(err)
	}

	if to.Before(from) {
		log.Fatal("--to must be on or after --from")
	}

	authToken, err := loadFyersAccessToken()
	if err != nil {
		log.Fatal(err)
	}

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

	symbols, err := ops.FetchActiveOHLCVSymbols(ctx, pgPool)
	if err != nil {
		log.Fatal("Failed to fetch active OHLCV symbols: ", err)
	}

	total := int64(len(symbols))
	printOHLCVProgress(0, total, 0)
	if total == 0 {
		return
	}

	jobs := make(chan models.Symbol)
	var wg sync.WaitGroup
	var doneCount atomic.Int64
	var errorCount atomic.Int64

	for range ohlcvWorkers {
		wg.Add(1)
		go func() {
			defer wg.Done()

			for symbol := range jobs {
				if err := fetchAndInsertOHLCV(ctx, pgPool, authToken, symbol, spec, from, to); err != nil {
					errorCount.Add(1)
					log.Printf("ohlcv fetch failed for %s (%s): %v", symbol.Symbol, symbol.ISIN, err)
				}

				done := doneCount.Add(1)
				printOHLCVProgress(done, total, errorCount.Load())
			}
		}()
	}

	for _, symbol := range symbols {
		jobs <- symbol
	}

	close(jobs)
	wg.Wait()
}

func loadFyersAccessToken() (string, error) {
	cfg, err := config.Load("internal/config/fyers.yaml")
	if err != nil {
		return "", fmt.Errorf("load fyers config: %w", err)
	}

	a := auth.New(cfg.Fyers)
	if err := a.LoadToken(); err != nil {
		return "", fmt.Errorf("load fyers token: %w", err)
	}

	if err := a.Validate(); err != nil {
		return "", fmt.Errorf("validate fyers token: %w", err)
	}

	accessToken := a.AccessToken()
	if accessToken == "" {
		return "", fmt.Errorf("fyers access token is empty")
	}

	return accessToken, nil
}

func parseOHLCVDate(raw string, flagName string) (time.Time, error) {
	if raw == "" {
		return time.Time{}, fmt.Errorf("%s is required", flagName)
	}

	value, err := time.Parse(ohlcvDateLayout, raw)
	if err != nil {
		return time.Time{}, fmt.Errorf("invalid %s %q (expected YYYY-MM-DD)", flagName, raw)
	}

	return value, nil
}

func fetchAndInsertOHLCV(
	ctx context.Context,
	pgPool *pgxpool.Pool,
	authToken string,
	symbol models.Symbol,
	spec ohlcvResolutionSpec,
	from time.Time,
	to time.Time,
) error {
	if symbol.ISIN == "" {
		return fmt.Errorf("missing isin")
	}

	if symbol.FySymbol == "" {
		return fmt.Errorf("missing fy_symbol")
	}

	for _, chunk := range buildOHLCVDateChunks(from, to, spec.MaxDays) {
		candles, err := spec.Fetch(authToken, symbol.FySymbol, symbol.ISIN, chunk.From, chunk.To)
		if err != nil {
			return fmt.Errorf(
				"fetch %s chunk %s..%s: %w",
				symbol.FySymbol,
				chunk.From.Format(ohlcvDateLayout),
				chunk.To.Format(ohlcvDateLayout),
				err,
			)
		}

		if len(candles) == 0 {
			continue
		}

		if err := ops.InsertOHLCVBatch(ctx, pgPool, spec.Table, candles); err != nil {
			return fmt.Errorf(
				"insert %s chunk %s..%s: %w",
				symbol.FySymbol,
				chunk.From.Format(ohlcvDateLayout),
				chunk.To.Format(ohlcvDateLayout),
				err,
			)
		}
	}

	return nil
}

func buildOHLCVDateChunks(from, to time.Time, maxDays int) []ohlcvDateChunk {
	chunks := make([]ohlcvDateChunk, 0, 1)
	chunkStart := from
	for !chunkStart.After(to) {
		chunkEnd := chunkStart.AddDate(0, 0, maxDays-1)
		if chunkEnd.After(to) {
			chunkEnd = to
		}

		chunks = append(chunks, ohlcvDateChunk{
			From: chunkStart,
			To:   chunkEnd,
		})

		chunkStart = chunkEnd.AddDate(0, 0, 1)
	}

	return chunks
}

func printOHLCVProgress(done, total, errors int64) {
	payload, err := json.Marshal(ohlcvProgress{
		Done:   done,
		Total:  total,
		Errors: errors,
	})
	if err != nil {
		log.Printf("marshal ohlcv progress: %v", err)
		return
	}

	fmt.Println(string(payload))
}
