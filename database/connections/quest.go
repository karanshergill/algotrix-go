package connections

import (
    "context"
    "fmt"
    "log"

    "github.com/jackc/pgx/v5/pgxpool"
    qdb "github.com/questdb/go-questdb-client/v4"
)

// NewQuestDBSender creates an ILP sender for high-throughput writes (port 9009).
func NewQuestDBSender(ctx context.Context, cfg *DBConfig) (qdb.LineSender, error) {
    addr := fmt.Sprintf("%s:%s", cfg.Host, cfg.ILPPort)
	sender, err := qdb.LineSenderFromConf(ctx, fmt.Sprintf("tcp::addr=%s;", addr))
    if err != nil {
        return nil, fmt.Errorf("failed to create questdb sender: %w", err)
    }

    log.Printf("connected to questdb ILP: %s", addr)
    return sender, nil
}

// NewQuestDBPool creates a pgx pool for reads/queries (port 8812).
func NewQuestDBPool(ctx context.Context, cfg *DBConfig) (*pgxpool.Pool, error) {
    pool, err := pgxpool.New(ctx, cfg.DSN())
    if err != nil {
        return nil, fmt.Errorf("failed to create questdb pool: %w", err)
	}

    if err := pool.Ping(ctx); err != nil {
        pool.Close()
        return nil, fmt.Errorf("failed to ping questdb: %w", err)
    }

    log.Printf("connected to questdb pgwire: %s:%s/%s", cfg.Host, cfg.Port, cfg.Database)
    return pool, nil
}