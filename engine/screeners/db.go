package screeners

import (
	"context"
	"fmt"
	"log"

	"github.com/jackc/pgx/v5/pgxpool"
)

// SignalDB handles signal persistence to atdb.
type SignalDB struct {
	pool *pgxpool.Pool
}

// NewSignalDB creates a SignalDB using the provided atdb pool.
func NewSignalDB(pool *pgxpool.Pool) *SignalDB {
	return &SignalDB{pool: pool}
}

// PersistSignal writes a signal to the signals table with dedup.
func (db *SignalDB) PersistSignal(sig *Signal, sessionDate string) error {
	dedupKey := fmt.Sprintf("%s:%s:%s", sig.ScreenerName, sig.ISIN, sessionDate)

	_, err := db.pool.Exec(context.Background(),
		`INSERT INTO signals (session_date, triggered_at, screener_name, isin, trading_symbol,
		 signal_type, trigger_price, threshold_price, ltp, percent_above, metadata, dedup_key)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
		 ON CONFLICT (dedup_key) DO NOTHING`,
		sessionDate, sig.TriggeredAt, sig.ScreenerName, sig.ISIN, sig.Symbol,
		string(sig.SignalType), sig.TriggerPrice, sig.ThresholdPrice, sig.LTP,
		sig.PercentAbove, sig.Metadata, dedupKey)

	if err != nil {
		log.Printf("[screener-db] persist failed for %s/%s: %v", sig.ScreenerName, sig.ISIN, err)
	}
	return err
}

// Close closes the database connection pool.
func (db *SignalDB) Close() {
	db.pool.Close()
}
