package ops

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

type Index struct {
	ID        int    `json:"id"`
	Symbol    string `json:"symbol"`
	Name      string `json:"name"`
	FySymbol  string `json:"fy_symbol"`
	Category  string `json:"category"`
	IsActive  bool   `json:"is_active"`
}

// FetchActiveIndices returns fy_symbol for all active indices.
func FetchActiveIndices(ctx context.Context, pool *pgxpool.Pool) ([]string, error) {
	rows, err := pool.Query(ctx, `
		SELECT fy_symbol FROM indices WHERE is_active = true ORDER BY category, name
	`)
	if err != nil {
		return nil, fmt.Errorf("fetch active indices: %w", err)
	}
	defer rows.Close()

	var symbols []string
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return nil, err
		}
		symbols = append(symbols, s)
	}
	return symbols, rows.Err()
}

// FetchAllIndices returns full index records.
func FetchAllIndices(ctx context.Context, pool *pgxpool.Pool) ([]Index, error) {
	rows, err := pool.Query(ctx, `
		SELECT id, symbol, name, fy_symbol, category, is_active
		FROM indices
		ORDER BY category, name
	`)
	if err != nil {
		return nil, fmt.Errorf("fetch indices: %w", err)
	}
	defer rows.Close()

	var indices []Index
	for rows.Next() {
		var idx Index
		if err := rows.Scan(&idx.ID, &idx.Symbol, &idx.Name, &idx.FySymbol, &idx.Category, &idx.IsActive); err != nil {
			return nil, err
		}
		indices = append(indices, idx)
	}
	return indices, rows.Err()
}
