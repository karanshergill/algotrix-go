package connections

import (
	"context"
	"fmt"
	"log"
	
	"github.com/jackc/pgx/v5/pgxpool"
)

func NewPostgresPool(ctx context.Context, cfg *DBConfig) (*pgxpool.Pool, error) {
    pool, err := pgxpool.New(ctx, cfg.DSN())
    if err != nil {
        return nil, fmt.Errorf("failed to create postgres pool: %w", err)
    }

    if err := pool.Ping(ctx); err != nil {
        pool.Close()
		return nil, fmt.Errorf("failed to ping postgres: %w", err)
    }

    log.Printf("connected to postgres: %s:%s/%s", cfg.Host, cfg.Port, cfg.Database)
    return pool, nil
}