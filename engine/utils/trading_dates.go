package utils

import (
	"database/sql"
	"fmt"
)

// TradingDates returns the last N distinct trading dates from nse_cm_bhavcopy,
// ordered newest first. Returns fewer than N if the table has fewer distinct dates.
func TradingDates(db *sql.DB, days int) ([]string, error) {
	if days <= 0 {
		return nil, fmt.Errorf("days must be positive, got %d", days)
	}

	rows, err := db.Query(
		`SELECT DISTINCT date FROM nse_cm_bhavcopy ORDER BY date DESC LIMIT $1`, days,
	)
	if err != nil {
		return nil, fmt.Errorf("querying trading dates: %w", err)
	}
	defer rows.Close()

	var dates []string
	for rows.Next() {
		var d string
		if err := rows.Scan(&d); err != nil {
			return nil, fmt.Errorf("scanning date: %w", err)
		}
		dates = append(dates, d)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating dates: %w", err)
	}

	return dates, nil
}
