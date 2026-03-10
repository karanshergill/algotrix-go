package ops

import (
	"context"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/karanshergill/algotrix-go/models"
)

// FetchActiveOHLCVSymbols returns active symbols with the Fyers mapping needed for OHLCV backfills.
func FetchActiveOHLCVSymbols(ctx context.Context, pool *pgxpool.Pool) ([]models.Symbol, error) {
	activePredicate, err := resolveActiveSymbolPredicate(ctx, pool)
	if err != nil {
		return nil, err
	}

	fySymbolColumn, err := resolveFySymbolColumn(ctx, pool)
	if err != nil {
		return nil, err
	}

	query := fmt.Sprintf(`
SELECT isin, symbol, %s AS fy_symbol
FROM symbols
WHERE %s
ORDER BY symbol
`, fySymbolColumn, activePredicate)

	rows, err := pool.Query(ctx, query)
	if err != nil {
		return nil, fmt.Errorf("fetch active ohlcv symbols: %w", err)
	}
	defer rows.Close()

	symbols := make([]models.Symbol, 0)
	for rows.Next() {
		var symbol models.Symbol
		if err := rows.Scan(&symbol.ISIN, &symbol.Symbol, &symbol.FySymbol); err != nil {
			return nil, fmt.Errorf("scan active ohlcv symbol: %w", err)
		}
		symbols = append(symbols, symbol)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate active ohlcv symbols: %w", err)
	}

	return symbols, nil
}

func resolveActiveSymbolPredicate(ctx context.Context, pool *pgxpool.Pool) (string, error) {
	statusExists, err := symbolColumnExists(ctx, pool, "status")
	if err != nil {
		return "", err
	}
	if statusExists {
		return "status = 'active'", nil
	}

	isActiveExists, err := symbolColumnExists(ctx, pool, "is_active")
	if err != nil {
		return "", err
	}
	if isActiveExists {
		return "is_active = true", nil
	}

	return "", fmt.Errorf("symbols table is missing both status and is_active columns")
}

func resolveFySymbolColumn(ctx context.Context, pool *pgxpool.Pool) (string, error) {
	fySymbolExists, err := symbolColumnExists(ctx, pool, "fy_symbol")
	if err != nil {
		return "", err
	}
	if fySymbolExists {
		return "fy_symbol", nil
	}

	fyersSymbolExists, err := symbolColumnExists(ctx, pool, "fyers_symbol")
	if err != nil {
		return "", err
	}
	if fyersSymbolExists {
		return "fyers_symbol", nil
	}

	return "", fmt.Errorf("symbols table is missing both fy_symbol and fyers_symbol columns")
}

func symbolColumnExists(ctx context.Context, pool *pgxpool.Pool, columnName string) (bool, error) {
	var exists bool
	err := pool.QueryRow(ctx, `
SELECT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'symbols'
      AND column_name = $1
)
`, strings.ToLower(columnName)).Scan(&exists)
	if err != nil {
		return false, fmt.Errorf("check symbols.%s column: %w", columnName, err)
	}
	return exists, nil
}

// InsertOHLCVBatch writes candles into the target OHLCV table using pgx CopyFrom for bulk throughput.
func InsertOHLCVBatch(ctx context.Context, pool *pgxpool.Pool, table string, candles []models.OHLCV) error {
	if len(candles) == 0 {
		return nil
	}

	rows := make([][]any, 0, len(candles))
	for _, candle := range candles {
		rows = append(rows, []any{
			candle.ISIN,
			candle.Open,
			candle.High,
			candle.Low,
			candle.Close,
			candle.Volume,
			candle.Timestamp,
		})
	}

	_, err := pool.CopyFrom(
		ctx,
		pgx.Identifier{table},
		[]string{"isin", "open", "high", "low", "close", "volume", "timestamp"},
		pgx.CopyFromRows(rows),
	)
	if err != nil {
		return fmt.Errorf("copy into %s: %w", table, err)
	}

	return nil
}
