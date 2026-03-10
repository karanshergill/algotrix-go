package conns

import (
	"fmt"
    "os"

    "gopkg.in/yaml.v3"
)

type DBConfig struct {
    Host     string `yaml:"host"`
    Port     string `yaml:"port"`
    User     string `yaml:"user"`
    Password string `yaml:"password"`
    Database string `yaml:"database"`
}

type DBsConfig struct {
	Postgres DBConfig `yaml:"postgres"`
}

func LoadDBConfig(path string) (*DBsConfig, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return nil, fmt.Errorf("failed to read db config: %w", err)
    }

    var cfg DBsConfig
    if err := yaml.Unmarshal(data, &cfg); err != nil {
        return nil, fmt.Errorf("failed to parse db config: %w", err)
    }
	    return &cfg, nil
}

func (c *DBConfig) DSN() string {
    return fmt.Sprintf("postgres://%s:%s@%s:%s/%s?sslmode=disable",
        c.User, c.Password, c.Host, c.Port, c.Database,
    )
}