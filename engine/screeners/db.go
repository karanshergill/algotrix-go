package screeners

import (
	"context"
	"fmt"
	"log"

	"github.com/jackc/pgx/v5/pgxpool"
)

// SignalDB handles signal persistence to the algotrix database.
type SignalDB struct {
	pool      *pgxpool.Pool
	isinToSID map[string]int    // ISIN -> security_id
	isinToSym map[string]string // ISIN -> trading_symbol
}

// NewSignalDB creates a SignalDB connected to the algotrix database.
func NewSignalDB(ctx context.Context, dsn string) (*SignalDB, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("connect to algotrix DB: %w", err)
	}

	db := &SignalDB{
		pool:      pool,
		isinToSID: make(map[string]int),
		isinToSym: make(map[string]string),
	}

	// Load scrip_master mapping
	rows, err := pool.Query(ctx,
		`SELECT isin, security_id, trading_symbol FROM scrip_master WHERE isin IS NOT NULL AND isin != ''`)
	if err != nil {
		return nil, fmt.Errorf("load scrip_master: %w", err)
	}
	defer rows.Close()

	for rows.Next() {
		var isin, sym string
		var sid int
		if err := rows.Scan(&isin, &sid, &sym); err != nil {
			return nil, err
		}
		db.isinToSID[isin] = sid
		db.isinToSym[isin] = sym
	}
	log.Printf("[screener-db] Loaded %d ISIN->security_id mappings", len(db.isinToSID))

	return db, nil
}

// PersistSignal writes a signal to the signals table with dedup.
func (db *SignalDB) PersistSignal(sig *Signal, sessionDate string) error {
	sid, ok := db.isinToSID[sig.ISIN]
	if !ok {
		log.Printf("[screener-db] ISIN %s not in scrip_master, skipping persist", sig.ISIN)
		return nil
	}

	tradingSym := sig.Symbol
	if s, ok := db.isinToSym[sig.ISIN]; ok && s != "" {
		tradingSym = s
	}

	dedupKey := fmt.Sprintf("%s:%d:%s", sig.ScreenerName, sid, sessionDate)

	_, err := db.pool.Exec(context.Background(),
		`INSERT INTO signals (session_date, triggered_at, screener_name, security_id, trading_symbol,
		 signal_type, trigger_price, threshold_price, ltp, percent_above, metadata, dedup_key)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
		 ON CONFLICT (dedup_key) DO NOTHING`,
		sessionDate, sig.TriggeredAt, sig.ScreenerName, sid, tradingSym,
		string(sig.SignalType), sig.TriggerPrice, sig.ThresholdPrice, sig.LTP,
		sig.PercentAbove, sig.Metadata, dedupKey)

	return err
}

// Close closes the database connection pool.
func (db *SignalDB) Close() {
	db.pool.Close()
}
