package features

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDefaultConfig(t *testing.T) {
	cfg := DefaultConfig()

	// Engine buffers
	if cfg.TickBuffer != 100_000 {
		t.Errorf("TickBuffer = %d, want 100000", cfg.TickBuffer)
	}
	if cfg.DepthBuffer != 50_000 {
		t.Errorf("DepthBuffer = %d, want 50000", cfg.DepthBuffer)
	}
	if cfg.SnapshotCloneStrategy != "copy_on_write" {
		t.Errorf("SnapshotCloneStrategy = %q, want copy_on_write", cfg.SnapshotCloneStrategy)
	}

	// Windows
	if cfg.Windows.Volume1m != "60s" {
		t.Errorf("Windows.Volume1m = %q, want 60s", cfg.Windows.Volume1m)
	}
	if cfg.Windows.Volume5m != "300s" {
		t.Errorf("Windows.Volume5m = %q, want 300s", cfg.Windows.Volume5m)
	}

	// Buffer capacity
	if cfg.RollingBufferCapacity.PerStock1m != 1000 {
		t.Errorf("PerStock1m = %d, want 1000", cfg.RollingBufferCapacity.PerStock1m)
	}
	if cfg.RollingBufferCapacity.PerStock5m != 5000 {
		t.Errorf("PerStock5m = %d, want 5000", cfg.RollingBufferCapacity.PerStock5m)
	}

	// Guards
	if cfg.GuardConfig.MaxPriceJumpPct != 20.0 {
		t.Errorf("MaxPriceJumpPct = %f, want 20.0", cfg.GuardConfig.MaxPriceJumpPct)
	}
	if cfg.GuardConfig.MaxSpreadBps != 500.0 {
		t.Errorf("MaxSpreadBps = %f, want 500.0", cfg.GuardConfig.MaxSpreadBps)
	}
	if cfg.GuardConfig.MinLTP != 0.01 {
		t.Errorf("MinLTP = %f, want 0.01", cfg.GuardConfig.MinLTP)
	}
	if !cfg.GuardConfig.AllowVolumeReset {
		t.Error("AllowVolumeReset = false, want true")
	}

	// Baselines
	if cfg.Baselines.ATRTradingDays != 14 {
		t.Errorf("ATRTradingDays = %d, want 14", cfg.Baselines.ATRTradingDays)
	}
	if cfg.Baselines.VolumeSlotTradingDays != 10 {
		t.Errorf("VolumeSlotTradingDays = %d, want 10", cfg.Baselines.VolumeSlotTradingDays)
	}
	if cfg.Baselines.MinSlotSamples != 5 {
		t.Errorf("MinSlotSamples = %d, want 5", cfg.Baselines.MinSlotSamples)
	}

	// Session
	if cfg.Session.PreOpenStart != "09:00" {
		t.Errorf("PreOpenStart = %q, want 09:00", cfg.Session.PreOpenStart)
	}
	if cfg.Session.MarketOpen != "09:15" {
		t.Errorf("MarketOpen = %q, want 09:15", cfg.Session.MarketOpen)
	}
	if cfg.Session.MarketClose != "15:30" {
		t.Errorf("MarketClose = %q, want 15:30", cfg.Session.MarketClose)
	}
	if !cfg.Session.RejectOutsideSession {
		t.Error("RejectOutsideSession = false, want true")
	}

	// Book weights
	expected := []float64{5, 3, 2, 1, 0.5}
	if len(cfg.BookWeights) != len(expected) {
		t.Fatalf("BookWeights length = %d, want %d", len(cfg.BookWeights), len(expected))
	}
	for i, w := range expected {
		if cfg.BookWeights[i] != w {
			t.Errorf("BookWeights[%d] = %f, want %f", i, cfg.BookWeights[i], w)
		}
	}

	// REST
	if cfg.REST.Port != 3003 {
		t.Errorf("REST.Port = %d, want 3003", cfg.REST.Port)
	}
	if cfg.REST.ReadTimeout != "5s" {
		t.Errorf("REST.ReadTimeout = %q, want 5s", cfg.REST.ReadTimeout)
	}

	// Hub
	if cfg.Hub.Port != 3002 {
		t.Errorf("Hub.Port = %d, want 3002", cfg.Hub.Port)
	}
	if !cfg.Hub.BroadcastFeatures {
		t.Error("Hub.BroadcastFeatures = false, want true")
	}
}

func TestLoadConfig(t *testing.T) {
	// Write a partial YAML to a temp file — only override a few fields
	partial := `
engine:
  tick_channel_buffer: 200000

feed_guards:
  max_price_jump_pct: 10.0

session:
  market_close: "15:29"

book_weights: [4, 2, 1]

rest:
  port: 4000
`
	dir := t.TempDir()
	path := filepath.Join(dir, "test_features.yaml")
	if err := os.WriteFile(path, []byte(partial), 0644); err != nil {
		t.Fatal(err)
	}

	cfg, err := LoadConfig(path)
	if err != nil {
		t.Fatal(err)
	}

	// Overridden values
	if cfg.TickBuffer != 200_000 {
		t.Errorf("TickBuffer = %d, want 200000", cfg.TickBuffer)
	}
	if cfg.GuardConfig.MaxPriceJumpPct != 10.0 {
		t.Errorf("MaxPriceJumpPct = %f, want 10.0", cfg.GuardConfig.MaxPriceJumpPct)
	}
	if cfg.Session.MarketClose != "15:29" {
		t.Errorf("MarketClose = %q, want 15:29", cfg.Session.MarketClose)
	}
	if len(cfg.BookWeights) != 3 || cfg.BookWeights[0] != 4 {
		t.Errorf("BookWeights = %v, want [4 2 1]", cfg.BookWeights)
	}
	if cfg.REST.Port != 4000 {
		t.Errorf("REST.Port = %d, want 4000", cfg.REST.Port)
	}

	// Defaults preserved for fields not in partial YAML
	if cfg.DepthBuffer != 50_000 {
		t.Errorf("DepthBuffer = %d, want 50000 (default)", cfg.DepthBuffer)
	}
	if cfg.GuardConfig.MaxSpreadBps != 500.0 {
		t.Errorf("MaxSpreadBps = %f, want 500.0 (default)", cfg.GuardConfig.MaxSpreadBps)
	}
	if cfg.Windows.Volume1m != "60s" {
		t.Errorf("Windows.Volume1m = %q, want 60s (default)", cfg.Windows.Volume1m)
	}
	if cfg.Hub.Port != 3002 {
		t.Errorf("Hub.Port = %d, want 3002 (default)", cfg.Hub.Port)
	}
	if cfg.Baselines.ATRTradingDays != 14 {
		t.Errorf("ATRTradingDays = %d, want 14 (default)", cfg.Baselines.ATRTradingDays)
	}
}

func TestLoadConfig_MissingFile(t *testing.T) {
	_, err := LoadConfig("/nonexistent/path/features.yaml")
	if err == nil {
		t.Fatal("expected error for missing file, got nil")
	}
}
