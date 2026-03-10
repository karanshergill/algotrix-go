package feed

import (
	"fmt"
	"os"
	"os/signal"
	"syscall"
)

type Recorder struct {
	configPath string
	symbols    []string
	config     *Config
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

	logTS("[Recorder] starting with %d symbols", len(r.symbols))

	if cfg.Feed.TBT.Enabled {
		r.tbt = NewTBTFeed(cfg, token, r.symbols)
		if err := r.tbt.Start(); err != nil {
			return fmt.Errorf("start TBT feed: %w", err)
		}
		logTS("[Recorder] TBT feed started")
	} else {
		logTS("[Recorder] TBT feed disabled")
	}

	if cfg.Feed.DataSocket.Enabled {
		r.datasocket = NewDataSocketFeed(cfg, token, r.symbols)
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
	logTS("[Recorder] shutdown complete")
}
