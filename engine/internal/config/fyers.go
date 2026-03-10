package config

import (
    "fmt"
    "os"

    "gopkg.in/yaml.v3"
)

type FyersConfig struct {
    AppID       string `yaml:"app_id"`
    SecretKey   string `yaml:"secret_key"`
    RedirectURL string `yaml:"redirect_url"`
    TokenPath   string `yaml:"token_path"`
}

type Config struct {
    Fyers FyersConfig `yaml:"fyers"`
}

func Load(path string) (*Config, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return nil, fmt.Errorf("read config: %w", err)
    }

    var cfg Config
    if err := yaml.Unmarshal(data, &cfg); err != nil {
        return nil, fmt.Errorf("parse config: %w", err)
    }

	if cfg.Fyers.AppID == "" || cfg.Fyers.SecretKey == "" {
        return nil, fmt.Errorf("fyers.app_id and fyers.secret_key are required")
    }

    if cfg.Fyers.TokenPath == "" {
        cfg.Fyers.TokenPath = "token.json"
    }

    return &cfg, nil
}