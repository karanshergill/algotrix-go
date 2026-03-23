package feed

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/jackc/pgx/v5/pgxpool"
)

type Recorder struct {
	configPath string
	symbols    []string
	config     *Config
	pool       *pgxpool.Pool
	hub        *Hub
	tbt        *TBTFeed
	datasocket *DataSocketFeed
	onTickCb   TickCallback
}

func NewRecorder(configPath string, symbols []string) *Recorder {
	return &Recorder{
		configPath: configPath,
		symbols:    symbols,
	}
}

// buildSymbolToISIN queries the symbols table for a fy_symbol → isin map.
// The feed receives and resolves Fyers symbols (e.g. "NSE:RELIANCE-EQ"), so
// we key by fy_symbol to match what arrives from the websocket.
func buildSymbolToISIN(ctx context.Context, pool *pgxpool.Pool) (map[string]string, error) {
	rows, err := pool.Query(ctx, "SELECT fy_symbol, isin FROM symbols WHERE status = 'active'")
	if err != nil {
		return nil, fmt.Errorf("query symbol→isin map: %w", err)
	}
	defer rows.Close()

	m := make(map[string]string)
	for rows.Next() {
		var fySymbol, isin string
		if err := rows.Scan(&fySymbol, &isin); err != nil {
			return nil, fmt.Errorf("scan symbol→isin row: %w", err)
		}
		m[fySymbol] = isin
	}
	return m, rows.Err()
}

func (r *Recorder) Start(token string) error {
	cfg, err := LoadConfig(r.configPath)
	if err != nil {
		return fmt.Errorf("load feed config: %w", err)
	}
	r.config = cfg

	// Connect to PostgreSQL
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, cfg.Feed.Storage.PostgresDSN)
	if err != nil {
		return fmt.Errorf("connect to postgres: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		return fmt.Errorf("postgres ping failed: %w", err)
	}
	r.pool = pool
	logTS("[Recorder] connected to postgres")

	// Build fy_symbol → ISIN lookup map (single DB query, shared by both feeds).
	symbolToISIN, err := buildSymbolToISIN(ctx, pool)
	if err != nil {
		return fmt.Errorf("build symbol→isin map: %w", err)
	}
	logTS("[Recorder] symbol→isin map built: %d entries", len(symbolToISIN))

	logTS("[Recorder] starting with %d symbols", len(r.symbols))

	// Start internal WebSocket hub for live tick/depth broadcast.
	if cfg.Feed.Hub.Enabled {
		r.hub = NewHub(cfg.Feed.Hub.Port)
		if err := r.hub.Start(); err != nil {
			return fmt.Errorf("start hub: %w", err)
		}
		logTS("[Recorder] Hub started on port %d", cfg.Feed.Hub.Port)
	} else {
		logTS("[Recorder] Hub disabled")
	}

	if cfg.Feed.TBT.Enabled {
		r.tbt = NewTBTFeed(cfg, token, r.symbols, pool, symbolToISIN, r.hub)
		if err := r.tbt.Start(); err != nil {
			return fmt.Errorf("start TBT feed: %w", err)
		}
		logTS("[Recorder] TBT feed started")
	} else {
		logTS("[Recorder] TBT feed disabled")
	}

	if cfg.Feed.DataSocket.Enabled {
		r.datasocket = NewDataSocketFeed(cfg, token, r.symbols, pool, symbolToISIN, r.hub)
		if r.onTickCb != nil {
			r.datasocket.SetOnTick(r.onTickCb)
		}
		if err := r.datasocket.Start(); err != nil {
			return fmt.Errorf("start DataSocket feed: %w", err)
		}
		logTS("[Recorder] DataSocket feed started")
	} else {
		logTS("[Recorder] DataSocket feed disabled")
	}

	logTS("[Recorder] all feeds running, waiting for signals...")

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	sig := <-sigCh
	logTS("[Recorder] received signal: %v, shutting down...", sig)

	r.Stop()
	return nil
}

// SetOnTick registers a callback on the DataSocket feed for every valid tick.
// Must be called before Start().
func (r *Recorder) SetOnTick(cb TickCallback) {
	r.onTickCb = cb
}

// Hub returns the internal hub (nil if hub disabled).
func (r *Recorder) Hub() *Hub { return r.hub }

// Pool returns the DB connection pool (nil before Start).
func (r *Recorder) Pool() *pgxpool.Pool { return r.pool }

func (r *Recorder) Stop() {
	if r.tbt != nil {
		r.tbt.Stop()
	}
	if r.datasocket != nil {
		r.datasocket.Stop()
	}
	if r.hub != nil {
		r.hub.Stop()
	}
	if r.pool != nil {
		r.pool.Close()
	}
	logTS("[Recorder] shutdown complete")
}
