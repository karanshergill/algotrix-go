package features

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// ---------------------------------------------------------------------------
// Configuration structs — loaded from features.yaml
// ---------------------------------------------------------------------------

// WindowConfig holds durations for rolling windows (as strings, e.g. "60s").
type WindowConfig struct {
	Volume1m  string `yaml:"volume_1m"`
	Volume5m  string `yaml:"volume_5m"`
	BuyVol5m  string `yaml:"buy_vol_5m"`
	SellVol5m string `yaml:"sell_vol_5m"`
	Updates1m string `yaml:"updates_1m"`
	High5m    string `yaml:"high_5m"`
	Low5m     string `yaml:"low_5m"`
}

// BufferConfig holds rolling buffer capacities.
type BufferConfig struct {
	PerStock1m int `yaml:"per_stock_1m"`
	PerStock5m int `yaml:"per_stock_5m"`
}

// GuardConfigYAML is the YAML representation of feed guard thresholds.
// It maps to GuardConfig at runtime.
type GuardConfigYAML struct {
	MaxPriceJumpPct  float64 `yaml:"max_price_jump_pct"`
	MaxSpreadBps     float64 `yaml:"max_spread_bps"`
	MinLTP           float64 `yaml:"min_ltp"`
	AllowVolumeReset bool    `yaml:"allow_volume_reset"`
}

// ToGuardConfig converts the YAML representation to a GuardConfig.
func (g *GuardConfigYAML) ToGuardConfig() *GuardConfig {
	return &GuardConfig{
		MaxPriceJumpPct:  g.MaxPriceJumpPct,
		MaxSpreadBps:     g.MaxSpreadBps,
		MinLTP:           g.MinLTP,
		AllowVolumeReset: g.AllowVolumeReset,
	}
}

// BaselineConfig holds parameters for pre-loaded baselines.
type BaselineConfig struct {
	ATRTradingDays        int `yaml:"atr_trading_days"`
	VolumeSlotTradingDays int `yaml:"volume_slot_trading_days"`
	AvgVolumeTradingDays  int `yaml:"avg_volume_trading_days"`
	MinSlotSamples        int `yaml:"min_slot_samples"`
}

// SessionConfig holds trading session time boundaries (as strings).
type SessionConfig struct {
	PreOpenStart         string `yaml:"pre_open_start"`
	MarketOpen           string `yaml:"market_open"`
	MarketClose          string `yaml:"market_close"`
	RejectOutsideSession bool   `yaml:"reject_outside_session"`
}

// BookWeightsConfig is a slice of weights for weighted book imbalance.
type BookWeightsConfig []float64

// RESTConfig holds REST API server settings.
type RESTConfig struct {
	Port        int    `yaml:"port"`
	ReadTimeout string `yaml:"read_timeout"`
}

// HubConfig holds hub/WebSocket broadcast settings.
type HubConfig struct {
	Port              int  `yaml:"port"`
	BroadcastFeatures bool `yaml:"broadcast_features"`
}

// EngineConfigYAML is the top-level YAML structure for features.yaml.
type EngineConfigYAML struct {
	Engine struct {
		TickChannelBuffer     int    `yaml:"tick_channel_buffer"`
		DepthChannelBuffer    int    `yaml:"depth_channel_buffer"`
		SnapshotCloneStrategy string `yaml:"snapshot_clone_strategy"`
	} `yaml:"engine"`
	Windows              WindowConfig    `yaml:"windows"`
	RollingBufferCapacity BufferConfig   `yaml:"rolling_buffer_capacity"`
	FeedGuards           GuardConfigYAML `yaml:"feed_guards"`
	Baselines            BaselineConfig  `yaml:"baselines"`
	Session              SessionConfig   `yaml:"session"`
	BookWeights          BookWeightsConfig `yaml:"book_weights"`
	REST                 RESTConfig      `yaml:"rest"`
	Hub                  HubConfig       `yaml:"hub"`
}

// ---------------------------------------------------------------------------
// EngineConfig — the runtime config used by FeatureEngine.
// ---------------------------------------------------------------------------

// EngineConfig holds all configuration for the feature engine.
type EngineConfig struct {
	TickBuffer            int
	DepthBuffer           int
	SnapshotCloneStrategy string

	Windows              WindowConfig
	RollingBufferCapacity BufferConfig
	GuardConfig          *GuardConfig
	Baselines            BaselineConfig
	Session              SessionConfig
	BookWeights          BookWeightsConfig
	REST                 RESTConfig
	Hub                  HubConfig
}

// DefaultConfig returns the full default configuration matching the plan.
func DefaultConfig() *EngineConfig {
	return &EngineConfig{
		TickBuffer:            100_000,
		DepthBuffer:           50_000,
		SnapshotCloneStrategy: "copy_on_write",

		Windows: WindowConfig{
			Volume1m:  "60s",
			Volume5m:  "300s",
			BuyVol5m:  "300s",
			SellVol5m: "300s",
			Updates1m: "60s",
			High5m:    "300s",
			Low5m:     "300s",
		},

		RollingBufferCapacity: BufferConfig{
			PerStock1m: 1000,
			PerStock5m: 5000,
		},

		GuardConfig: &GuardConfig{
			MaxPriceJumpPct:  20.0,
			MaxSpreadBps:     500.0,
			MinLTP:           0.01,
			AllowVolumeReset: true,
		},

		Baselines: BaselineConfig{
			ATRTradingDays:        30,
			VolumeSlotTradingDays: 10,
			AvgVolumeTradingDays:  10,
			MinSlotSamples:        5,
		},

		Session: SessionConfig{
			PreOpenStart:         "09:00",
			MarketOpen:           "09:15",
			MarketClose:          "15:30",
			RejectOutsideSession: true,
		},

		BookWeights: BookWeightsConfig{5, 3, 2, 1, 0.5},

		REST: RESTConfig{
			Port:        3003,
			ReadTimeout: "5s",
		},

		Hub: HubConfig{
			Port:              3002,
			BroadcastFeatures: true,
		},
	}
}

// DefaultEngineConfig returns sensible defaults (alias for DefaultConfig).
func DefaultEngineConfig() *EngineConfig {
	return DefaultConfig()
}

// LoadConfig loads configuration from a YAML file.
// Missing fields are filled with defaults.
func LoadConfig(path string) (*EngineConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config %s: %w", path, err)
	}

	var raw EngineConfigYAML
	if err := yaml.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("parse config %s: %w", path, err)
	}

	// Parse into a raw map to detect which keys are explicitly set.
	var rawMap map[string]interface{}
	_ = yaml.Unmarshal(data, &rawMap)

	cfg := DefaultConfig()

	// Engine section
	if raw.Engine.TickChannelBuffer > 0 {
		cfg.TickBuffer = raw.Engine.TickChannelBuffer
	}
	if raw.Engine.DepthChannelBuffer > 0 {
		cfg.DepthBuffer = raw.Engine.DepthChannelBuffer
	}
	if raw.Engine.SnapshotCloneStrategy != "" {
		cfg.SnapshotCloneStrategy = raw.Engine.SnapshotCloneStrategy
	}

	// Windows
	if raw.Windows.Volume1m != "" {
		cfg.Windows.Volume1m = raw.Windows.Volume1m
	}
	if raw.Windows.Volume5m != "" {
		cfg.Windows.Volume5m = raw.Windows.Volume5m
	}
	if raw.Windows.BuyVol5m != "" {
		cfg.Windows.BuyVol5m = raw.Windows.BuyVol5m
	}
	if raw.Windows.SellVol5m != "" {
		cfg.Windows.SellVol5m = raw.Windows.SellVol5m
	}
	if raw.Windows.Updates1m != "" {
		cfg.Windows.Updates1m = raw.Windows.Updates1m
	}
	if raw.Windows.High5m != "" {
		cfg.Windows.High5m = raw.Windows.High5m
	}
	if raw.Windows.Low5m != "" {
		cfg.Windows.Low5m = raw.Windows.Low5m
	}

	// Buffer capacity
	if raw.RollingBufferCapacity.PerStock1m > 0 {
		cfg.RollingBufferCapacity.PerStock1m = raw.RollingBufferCapacity.PerStock1m
	}
	if raw.RollingBufferCapacity.PerStock5m > 0 {
		cfg.RollingBufferCapacity.PerStock5m = raw.RollingBufferCapacity.PerStock5m
	}

	// Feed guards
	if raw.FeedGuards.MaxPriceJumpPct > 0 {
		cfg.GuardConfig.MaxPriceJumpPct = raw.FeedGuards.MaxPriceJumpPct
	}
	if raw.FeedGuards.MaxSpreadBps > 0 {
		cfg.GuardConfig.MaxSpreadBps = raw.FeedGuards.MaxSpreadBps
	}
	if raw.FeedGuards.MinLTP > 0 {
		cfg.GuardConfig.MinLTP = raw.FeedGuards.MinLTP
	}
	// AllowVolumeReset: only override if explicitly set in YAML
	if guards, ok := rawMap["feed_guards"].(map[string]interface{}); ok {
		if _, set := guards["allow_volume_reset"]; set {
			cfg.GuardConfig.AllowVolumeReset = raw.FeedGuards.AllowVolumeReset
		}
	}

	// Baselines
	if raw.Baselines.ATRTradingDays > 0 {
		cfg.Baselines.ATRTradingDays = raw.Baselines.ATRTradingDays
	}
	if raw.Baselines.VolumeSlotTradingDays > 0 {
		cfg.Baselines.VolumeSlotTradingDays = raw.Baselines.VolumeSlotTradingDays
	}
	if raw.Baselines.AvgVolumeTradingDays > 0 {
		cfg.Baselines.AvgVolumeTradingDays = raw.Baselines.AvgVolumeTradingDays
	}
	if raw.Baselines.MinSlotSamples > 0 {
		cfg.Baselines.MinSlotSamples = raw.Baselines.MinSlotSamples
	}

	// Session
	if raw.Session.PreOpenStart != "" {
		cfg.Session.PreOpenStart = raw.Session.PreOpenStart
	}
	if raw.Session.MarketOpen != "" {
		cfg.Session.MarketOpen = raw.Session.MarketOpen
	}
	if raw.Session.MarketClose != "" {
		cfg.Session.MarketClose = raw.Session.MarketClose
	}
	// RejectOutsideSession: only override if explicitly set in YAML
	if session, ok := rawMap["session"].(map[string]interface{}); ok {
		if _, set := session["reject_outside_session"]; set {
			cfg.Session.RejectOutsideSession = raw.Session.RejectOutsideSession
		}
	}

	// Book weights
	if len(raw.BookWeights) > 0 {
		cfg.BookWeights = raw.BookWeights
	}

	// REST
	if raw.REST.Port > 0 {
		cfg.REST.Port = raw.REST.Port
	}
	if raw.REST.ReadTimeout != "" {
		cfg.REST.ReadTimeout = raw.REST.ReadTimeout
	}

	// Hub
	if raw.Hub.Port > 0 {
		cfg.Hub.Port = raw.Hub.Port
	}
	// BroadcastFeatures: only override if explicitly set in YAML
	if hub, ok := rawMap["hub"].(map[string]interface{}); ok {
		if _, set := hub["broadcast_features"]; set {
			cfg.Hub.BroadcastFeatures = raw.Hub.BroadcastFeatures
		}
	}

	return cfg, nil
}
