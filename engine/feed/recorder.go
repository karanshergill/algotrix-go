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
	tbt        *TBTFeed
	datasocket *DataSocketFeed
}

func NewRecorder(configPath string, symbols []string) *Recorder {
	return &Recorder{
		configPath: configPath,
		symbols:    symbols,
	}
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

	logTS("[Recorder] starting with %d symbols", len(r.symbols))

	if cfg.Feed.TBT.Enabled {
		r.tbt = NewTBTFeed(cfg, token, r.symbols, pool)
		if err := r.tbt.Start(); err != nil {
			return fmt.Errorf("start TBT feed: %w", err)
		}
		logTS("[Recorder] TBT feed started")
	} else {
		logTS("[Recorder] TBT feed disabled")
	}

	if cfg.Feed.DataSocket.Enabled {
		r.datasocket = NewDataSocketFeed(cfg, token, r.symbols, pool)
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

func (r *Recorder) Stop() {
	if r.tbt != nil {
		r.tbt.Stop()
	}
	if r.datasocket != nil {
		r.datasocket.Stop()
	}
	if r.pool != nil {
		r.pool.Close()
	}
	logTS("[Recorder] shutdown complete")
}
