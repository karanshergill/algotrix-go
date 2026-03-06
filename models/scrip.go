package models

import "time"

// Scrip represents a single row in the nse_cm_scrips table.
type Scrip struct {
	// Identity
	ISIN        string
	CompanyName string
	FaceValue   float64

	// Classification
	SectorMacro     string
	Sector          string
	Industry        string
	IndustryBasic   string
	ListingDate     time.Time
	IndexMembership []string

	// Flags
	IsFNO           bool
	IsSME           bool
	IsPSU           bool
	PromoterPledged bool

	// Valuation
	MarketCap          int64
	FreeFloatMarketCap int64
	TotalShares        int64

	// Ratios
	PESymbol float64
	PESector float64

	// Shareholding
	PromoterPct         float64
	PublicPct           float64
	FIIPct              float64
	DIIPct              float64
	MutualFundPct       float64
	InsurancePct        float64
	RetailPct           float64
	ShareholdingQuarter string
}
