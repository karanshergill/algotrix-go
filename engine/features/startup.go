package features

import (
	"context"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/karanshergill/algotrix-go/feed"
)

// StartFeatureEngine is the high-level startup function that wires everything together.
// It loads config, connects to DB, registers stocks, preloads baselines, and starts
// the engine event loop + REST server.
// Accepts dbDSN string — creates its own pool.
func StartFeatureEngine(ctx context.Context, dbDSN string, hub *feed.Hub) (*FeatureEngine, *FeedAdapter, error) {
	pool, err := pgxpool.New(ctx, dbDSN)
	if err != nil {
		return nil, nil, fmt.Errorf("connect to DB: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, nil, fmt.Errorf("DB ping: %w", err)
	}
	return StartFeatureEngineWithPool(ctx, pool, hub)
}

// StartFeatureEngineWithPool starts the feature engine using an existing DB pool.
func StartFeatureEngineWithPool(ctx context.Context, pool *pgxpool.Pool, hub *feed.Hub) (*FeatureEngine, *FeedAdapter, error) {
	// 1. Load config (use defaults if file missing)
	config, err := loadConfigOrDefault("features.yaml")
	if err != nil {
		return nil, nil, fmt.Errorf("load config: %w", err)
	}

	// 2. Create engine
	engine := NewFeatureEngine(config)
	log.Printf("[startup] feature engine created (tick_buf=%d, depth_buf=%d)", config.TickBuffer, config.DepthBuffer)

	log.Println("[startup] using provided database pool")

	// 4-5. Register stocks from DB
	registered, err := RegisterStocksFromDB(ctx, pool, engine)
	if err != nil {
		pool.Close()
		return nil, nil, fmt.Errorf("register stocks: %w", err)
	}
	log.Printf("[startup] registered %d stocks from nse_cm_bhavcopy", registered)

	// 6. Register sectors
	for _, name := range GetSectorNames() {
		if sec, ok := engine.sectors[name]; ok {
			_ = sec // already registered via baselines
		} else {
			engine.RegisterSector(name, nil)
		}
	}
	log.Printf("[startup] registered %d sectors", len(engine.sectors))

	// 7. Preload baselines
	if err := PreloadBaselines(ctx, pool, engine.stocks, engine.sectors); err != nil {
		log.Printf("[startup] WARNING: preload baselines failed: %v", err)
		// Non-fatal — engine can run without baselines (quality flags will indicate missing)
	} else {
		log.Println("[startup] baselines preloaded")
	}

	// 8. Create feed adapter
	adapter := NewFeedAdapter(engine, hub)
	log.Println("[startup] feed adapter created")

	// 9. Start engine event loop
	go engine.Run(ctx)
	log.Println("[startup] engine event loop started")

	// 10. Start REST server
	restServer := NewRESTServer(engine, config.REST.Port)
	go func() {
		if err := restServer.Start(ctx); err != nil {
			log.Printf("[startup] REST server error: %v", err)
		}
	}()
	log.Printf("[startup] REST server starting on port %d", config.REST.Port)

	// 11. Start session
	engine.session.SessionStart(time.Now())
	log.Println("[startup] session started")

	return engine, adapter, nil
}

// RegisterStocksFromDB queries distinct ISINs + symbols from the latest trade_date
// in nse_cm_bhavcopy and registers them on the engine.
func RegisterStocksFromDB(ctx context.Context, pool *pgxpool.Pool, engine *FeatureEngine) (int, error) {
	rows, err := pool.Query(ctx,
		`SELECT s.isin, s.symbol FROM symbols s
		 WHERE s.status = 'active'
		 AND s.isin IN (
		   SELECT DISTINCT isin FROM nse_cm_bhavcopy
		   WHERE date = (SELECT MAX(date) FROM nse_cm_bhavcopy)
		 )`)
	if err != nil {
		return 0, fmt.Errorf("query stocks: %w", err)
	}
	defer rows.Close()

	count := 0
	for rows.Next() {
		var isin, symbol string
		if err := rows.Scan(&isin, &symbol); err != nil {
			return count, fmt.Errorf("scan stock row: %w", err)
		}
		engine.RegisterStock(isin, symbol, "") // sector assigned during baseline load
		count++
	}
	return count, rows.Err()
}

// loadConfigOrDefault loads config from path, falling back to defaults if the file is missing.
func loadConfigOrDefault(path string) (*EngineConfig, error) {
	if _, err := os.Stat(path); os.IsNotExist(err) {
		log.Printf("[startup] config file %s not found, using defaults", path)
		return DefaultConfig(), nil
	}
	cfg, err := LoadConfig(path)
	if err != nil {
		return nil, err
	}
	return cfg, nil
}
