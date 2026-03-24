package feed

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

type TBTConfig struct {
	Enabled              bool   `yaml:"enabled"`
	DiffOnly             bool   `yaml:"diff_only"`
	SnapshotIntervalMs   int    `yaml:"snapshot_interval_ms"`
	FlushIntervalMs      int    `yaml:"flush_interval_ms"`
	MaxDepthLevels       int    `yaml:"max_depth_levels"`
	Reconnect            bool   `yaml:"reconnect"`
	MaxReconnectAttempts int    `yaml:"max_reconnect_attempts"`
	LogPath              string `yaml:"log_path"`
}

type DataSocketConfig struct {
	Enabled              bool   `yaml:"enabled"`
	FlushIntervalMs      int    `yaml:"flush_interval_ms"`
	Reconnect            bool   `yaml:"reconnect"`
	MaxReconnectAttempts int    `yaml:"max_reconnect_attempts"`
	LogPath              string `yaml:"log_path"`
}

type StorageConfig struct {
	PostgresDSN string `yaml:"postgres_dsn"`
	DepthTable  string `yaml:"depth_table"`
	TicksTable  string `yaml:"ticks_table"`
}

type HubConfig struct {
	Enabled bool `yaml:"enabled"`
	Port    int  `yaml:"port"`
}

type FeedConfig struct {
	TBT        TBTConfig        `yaml:"tbt"`
	DataSocket DataSocketConfig `yaml:"datasocket"`
	Storage    StorageConfig    `yaml:"storage"`
	Hub        HubConfig        `yaml:"hub"`
}

type Config struct {
	Feed FeedConfig `yaml:"feed"`
}

func LoadConfig(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read feed config: %w", err)
	}

	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse feed config: %w", err)
	}

	// Defaults.
	if cfg.Feed.TBT.FlushIntervalMs <= 0 {
		cfg.Feed.TBT.FlushIntervalMs = 100
	}
	if cfg.Feed.TBT.MaxDepthLevels <= 0 || cfg.Feed.TBT.MaxDepthLevels > 50 {
		cfg.Feed.TBT.MaxDepthLevels = 50
	}
	if cfg.Feed.DataSocket.FlushIntervalMs <= 0 {
		cfg.Feed.DataSocket.FlushIntervalMs = 100
	}
	if cfg.Feed.Hub.Port <= 0 {
		cfg.Feed.Hub.Port = 3002
	}

	return &cfg, nil
}
