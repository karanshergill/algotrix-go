package operations

import (
	"context"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/karanshergill/algotrix-go/models"
)

const upsertScripSQL = `
INSERT INTO nse_cm_scrips (
    isin, company_name, face_value,
    sector_macro, sector, industry, industry_basic,
    listing_date, index_membership,
    is_fno, is_sme, is_psu, promoter_pledged,
    market_cap, free_float_market_cap, total_shares,
    pe_symbol, pe_sector,
    promoter_pct, public_pct, fii_pct, dii_pct,
    mutual_fund_pct, insurance_pct, retail_pct,
    shareholding_quarter
) VALUES (
    $1, $2, $3,
    $4, $5, $6, $7,
    $8, $9,
    $10, $11, $12, $13,
    $14, $15, $16,
    $17, $18,
    $19, $20, $21, $22,
    $23, $24, $25,
    $26
)
ON CONFLICT (isin) DO UPDATE SET
    company_name          = EXCLUDED.company_name,
    face_value            = EXCLUDED.face_value,
    sector_macro          = EXCLUDED.sector_macro,
    sector                = EXCLUDED.sector,
    industry              = EXCLUDED.industry,
    industry_basic        = EXCLUDED.industry_basic,
    listing_date          = EXCLUDED.listing_date,
    index_membership      = EXCLUDED.index_membership,
    is_fno                = EXCLUDED.is_fno,
    is_sme                = EXCLUDED.is_sme,
    is_psu                = EXCLUDED.is_psu,
    promoter_pledged      = EXCLUDED.promoter_pledged,
    market_cap            = EXCLUDED.market_cap,
    free_float_market_cap = EXCLUDED.free_float_market_cap,
    total_shares          = EXCLUDED.total_shares,
    pe_symbol             = EXCLUDED.pe_symbol,
    pe_sector             = EXCLUDED.pe_sector,
    promoter_pct          = EXCLUDED.promoter_pct,
    public_pct            = EXCLUDED.public_pct,
    fii_pct               = EXCLUDED.fii_pct,
    dii_pct               = EXCLUDED.dii_pct,
    mutual_fund_pct       = EXCLUDED.mutual_fund_pct,
    insurance_pct         = EXCLUDED.insurance_pct,
    retail_pct            = EXCLUDED.retail_pct,
    shareholding_quarter  = EXCLUDED.shareholding_quarter
`

const insertSkipSQL = `
INSERT INTO nse_cm_symbols_skip (symbol, isin, reason, detail)
VALUES ($1, $2, $3, $4)
ON CONFLICT (symbol) DO NOTHING
`

// UpsertScrip inserts or updates a single scrip record.
func UpsertScrip(ctx context.Context, pool *pgxpool.Pool, s *models.Scrip) error {
	_, err := pool.Exec(ctx, upsertScripSQL,
		s.ISIN, s.CompanyName, s.FaceValue,
		s.SectorMacro, s.Sector, s.Industry, s.IndustryBasic,
		s.ListingDate, s.IndexMembership,
		s.IsFNO, s.IsSME, s.IsPSU, s.PromoterPledged,
		s.MarketCap, s.FreeFloatMarketCap, s.TotalShares,
		s.PESymbol, s.PESector,
		s.PromoterPct, s.PublicPct, s.FIIPct, s.DIIPct,
		s.MutualFundPct, s.InsurancePct, s.RetailPct,
		s.ShareholdingQuarter,
	)
	if err != nil {
		return fmt.Errorf("upsert scrip %s: %w", s.ISIN, err)
	}
	return nil
}

// InsertSkip records a symbol to skip in future runs.
func InsertSkip(ctx context.Context, pool *pgxpool.Pool, symbol, isin, reason, detail string) error {
	_, err := pool.Exec(ctx, insertSkipSQL, symbol, isin, reason, detail)
	return err
}

// seriesReason maps Fyers symbol suffixes to skip reasons.
var seriesReason = map[string]string{
	"-BE": "trade_to_trade",
	"-SM": "sme",
	"-BL": "non_equity",
	"-IL": "non_equity",
	"-N0": "non_equity",
	"-N1": "non_equity",
	"-N2": "non_equity",
	"-N3": "non_equity",
	"-N4": "non_equity",
	"-N5": "non_equity",
	"-N6": "non_equity",
	"-AE": "non_equity",
	"-AF": "non_equity",
	"-T0": "non_equity",
}

// cleanSymbol converts "NSE:SBIN-EQ" → "SBIN".
func cleanSymbol(raw string) string {
	sym := strings.TrimPrefix(raw, "NSE:")
	// Strip the last -XX suffix.
	if idx := strings.LastIndex(sym, "-"); idx > 0 {
		sym = sym[:idx]
	}
	return sym
}

// CategorizeAndFetchSymbols loads all symbols from nse_cm_symbols,
// auto-inserts non-EQ symbols into nse_cm_symbols_skip, and returns
// only EQ symbols that are not in the skip table.
func CategorizeAndFetchSymbols(ctx context.Context, pool *pgxpool.Pool) ([]string, error) {
	// 1. Load ALL symbols.
	rows, err := pool.Query(ctx, "SELECT symbol, isin FROM nse_cm_symbols ORDER BY symbol")
	if err != nil {
		return nil, fmt.Errorf("querying symbols: %w", err)
	}
	defer rows.Close()

	type rawSym struct {
		symbol string
		isin   string
	}
	var allSymbols []rawSym
	for rows.Next() {
		var s rawSym
		if err := rows.Scan(&s.symbol, &s.isin); err != nil {
			return nil, err
		}
		allSymbols = append(allSymbols, s)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	// 2. Categorize non-EQ symbols and insert into skip table.
	for _, s := range allSymbols {
		if strings.HasSuffix(s.symbol, "-EQ") {
			continue
		}
		clean := cleanSymbol(s.symbol)
		reason := "non_equity"
		suffix := s.symbol[strings.LastIndex(s.symbol, "-"):]
		if r, ok := seriesReason[suffix]; ok {
			reason = r
		}
		_ = InsertSkip(ctx, pool, clean, s.isin, reason, "Series: "+suffix)
	}

	// 3. Return EQ symbols not in skip table.
	eqRows, err := pool.Query(ctx, `
		SELECT REPLACE(REPLACE(s.symbol, 'NSE:', ''), '-EQ', '')
		FROM nse_cm_symbols s
		LEFT JOIN nse_cm_symbols_skip sk
			ON REPLACE(REPLACE(s.symbol, 'NSE:', ''), '-EQ', '') = sk.symbol
		WHERE s.symbol LIKE '%-EQ'
			AND sk.symbol IS NULL
		ORDER BY s.symbol
	`)
	if err != nil {
		return nil, fmt.Errorf("querying eq symbols: %w", err)
	}
	defer eqRows.Close()

	var symbols []string
	for eqRows.Next() {
		var sym string
		if err := eqRows.Scan(&sym); err != nil {
			return nil, err
		}
		symbols = append(symbols, sym)
	}
	return symbols, eqRows.Err()
}
