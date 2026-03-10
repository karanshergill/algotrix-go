package ops

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

// UpsertScrip inserts or updates a single scrip enrichment record.
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

// seriesSkipReason maps non-EQ series to skip reasons.
var seriesSkipReason = map[string]string{
	"BE": "trade_to_trade",
	"SM": "sme",
	"ST": "sme",
	"BL": "non_equity",
	"IL": "non_equity",
	"SG": "non_equity",
	"GS": "non_equity",
	"GB": "non_equity",
	"MF": "non_equity",
	"TB": "non_equity",
	"BZ": "non_equity",
	"N0": "non_equity",
	"N1": "non_equity",
	"N2": "non_equity",
	"N3": "non_equity",
	"N4": "non_equity",
	"N5": "non_equity",
	"N6": "non_equity",
	"AE": "non_equity",
	"AF": "non_equity",
	"T0": "non_equity",
}

// CleanSymbol extracts the clean symbol name from Fyers format.
// "NSE:RELIANCE-EQ" → "RELIANCE"
func CleanSymbol(fySymbol string) string {
	sym := strings.TrimPrefix(fySymbol, "NSE:")
	if idx := strings.LastIndex(sym, "-"); idx > 0 {
		sym = sym[:idx]
	}
	return sym
}

// ExtractSeries extracts the series from Fyers format.
// "NSE:RELIANCE-EQ" → "EQ"
func ExtractSeries(fySymbol string) string {
	if idx := strings.LastIndex(fySymbol, "-"); idx > 0 && idx < len(fySymbol)-1 {
		return fySymbol[idx+1:]
	}
	return "UNKNOWN"
}

const upsertSymbolSQL = `
INSERT INTO symbols (isin, symbol, name, fy_token, fy_symbol, series, status, skip_reason, skip_detail, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
ON CONFLICT (isin) DO UPDATE SET
    symbol      = EXCLUDED.symbol,
    name        = EXCLUDED.name,
    fy_token    = EXCLUDED.fy_token,
    fy_symbol   = EXCLUDED.fy_symbol,
    series      = EXCLUDED.series,
    status      = EXCLUDED.status,
    skip_reason = EXCLUDED.skip_reason,
    skip_detail = EXCLUDED.skip_detail,
    updated_at  = now()
`

// UpsertSymbol inserts or updates a symbol in the unified symbols table.
// Automatically determines status and skip_reason from the series.
func UpsertSymbol(ctx context.Context, pool *pgxpool.Pool, isin, name string, fyToken int64, fySymbol string) error {
	symbol := CleanSymbol(fySymbol)
	series := ExtractSeries(fySymbol)

	status := "active"
	var skipReason *string
	var skipDetail *string

	if series != "EQ" {
		status = "skipped"
		reason := "non_equity"
		if r, ok := seriesSkipReason[series]; ok {
			reason = r
		}
		detail := "Series: " + series
		skipReason = &reason
		skipDetail = &detail
	}

	_, err := pool.Exec(ctx, upsertSymbolSQL,
		isin, symbol, name, fyToken, fySymbol, series, status, skipReason, skipDetail,
	)
	if err != nil {
		return fmt.Errorf("upsert symbol %s: %w", symbol, err)
	}
	return nil
}

// FetchActiveSymbols returns clean symbol names for all active (tradeable) symbols.
func FetchActiveSymbols(ctx context.Context, pool *pgxpool.Pool) ([]string, error) {
	rows, err := pool.Query(ctx, "SELECT symbol FROM symbols WHERE status = 'active' ORDER BY symbol")
	if err != nil {
		return nil, fmt.Errorf("fetch active symbols: %w", err)
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

// FetchActiveISINs returns ISINs for all active (tradeable) symbols.
func FetchActiveISINs(ctx context.Context, pool *pgxpool.Pool) ([]string, error) {
	rows, err := pool.Query(ctx, "SELECT isin FROM symbols WHERE status = 'active' ORDER BY symbol")
	if err != nil {
		return nil, fmt.Errorf("fetch active ISINs: %w", err)
	}
	defer rows.Close()

	var isins []string
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return nil, err
		}
		isins = append(isins, s)
	}
	return isins, rows.Err()
}

// FetchISINForSymbol returns the ISIN for a given clean symbol name.
func FetchISINForSymbol(ctx context.Context, pool *pgxpool.Pool, symbol string) (string, error) {
	var isin string
	err := pool.QueryRow(ctx, "SELECT isin FROM symbols WHERE symbol = $1 AND status = 'active'", symbol).Scan(&isin)
	if err != nil {
		return "", fmt.Errorf("fetch ISIN for %s: %w", symbol, err)
	}
	return isin, nil
}
