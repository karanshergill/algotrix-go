package nse

import (
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"strconv"
	"time"

	"github.com/karanshergill/algotrix-go/models"
)

// flexFloat handles NSE returning either a number or "-" string for numeric fields.
type flexFloat float64

func (f *flexFloat) UnmarshalJSON(b []byte) error {
	var raw any
	if err := json.Unmarshal(b, &raw); err != nil {
		return err
	}
	switch v := raw.(type) {
	case float64:
		*f = flexFloat(v)
	case string:
		if v == "-" || v == "" {
			*f = 0
			return nil
		}
		n, err := strconv.ParseFloat(v, 64)
		if err != nil {
			*f = 0
			return nil
		}
		*f = flexFloat(n)
	default:
		*f = 0
	}
	return nil
}

// flexStringSlice handles NSE returning either a string array or "-" string.
type flexStringSlice []string

func (s *flexStringSlice) UnmarshalJSON(b []byte) error {
	// Try array first.
	var arr []string
	if err := json.Unmarshal(b, &arr); err == nil {
		*s = arr
		return nil
	}
	// Fall back to string (e.g. "-").
	*s = nil
	return nil
}

// equityDetailsResponse maps the /api/quote-equity?symbol=X response.
type equityDetailsResponse struct {
	Info struct {
		CompanyName string `json:"companyName"`
		ISIN        string `json:"isin"`
		IsFNOSec    bool   `json:"isFNOSec"`
		IsETFSec    bool   `json:"isETFSec"`
	} `json:"info"`
	Metadata struct {
		ListingDate    string          `json:"listingDate"`
		PDSymbolPe     flexFloat       `json:"pdSymbolPe"`
		PDSectorPe     flexFloat       `json:"pdSectorPe"`
		PDSectorIndAll flexStringSlice `json:"pdSectorIndAll"`
	} `json:"metadata"`
	SecurityInfo struct {
		BoardStatus string    `json:"boardStatus"`
		FaceValue   flexFloat `json:"faceValue"`
		IssuedSize  int64     `json:"issuedSize"`
	} `json:"securityInfo"`
	IndustryInfo struct {
		Macro         string `json:"macro"`
		Sector        string `json:"sector"`
		Industry      string `json:"industry"`
		BasicIndustry string `json:"basicIndustry"`
	} `json:"industryInfo"`
}

// tradeInfoResponse maps the /api/quote-equity?symbol=X&section=trade_info response.
type tradeInfoResponse struct {
	MarketDeptOrderBook struct {
		TradeInfo struct {
			TotalMarketCap float64 `json:"totalMarketCap"`
			FFMC           float64 `json:"ffmc"`
		} `json:"tradeInfo"`
	} `json:"marketDeptOrderBook"`
}

// crToRupees converts crores (with decimals) to raw rupees as int64.
func crToRupees(cr float64) int64 {
	return int64(math.Round(cr * 1e7))
}

// parseNSEDate parses NSE date format "01-Mar-1995" to time.Time.
func parseNSEDate(s string) time.Time {
	t, err := time.Parse("02-Jan-2006", s)
	if err != nil {
		return time.Time{}
	}
	return t
}

// FetchEquityDetails fetches equity details and trade info for a symbol.
// Populates identity, classification, flags, valuation, and ratio fields.
// Shareholding fields are NOT populated here (see FetchShareholding).
func (c *Client) FetchEquityDetails(symbol string, scrip *models.Scrip) error {
	encoded := url.QueryEscape(symbol)

	// 1. Equity details.
	var details equityDetailsResponse
	if err := c.getJSON("/api/quote-equity?symbol="+encoded, &details); err != nil {
		return fmt.Errorf("equity details for %s: %w", symbol, err)
	}

	// Skip ETFs and mutual funds.
	if details.Info.IsETFSec {
		return fmt.Errorf("skipping ETF/MF: %s", symbol)
	}

	// Identity
	scrip.ISIN = details.Info.ISIN
	scrip.CompanyName = details.Info.CompanyName
	scrip.FaceValue = float64(details.SecurityInfo.FaceValue)

	// Classification
	scrip.SectorMacro = details.IndustryInfo.Macro
	scrip.Sector = details.IndustryInfo.Sector
	scrip.Industry = details.IndustryInfo.Industry
	scrip.IndustryBasic = details.IndustryInfo.BasicIndustry
	scrip.ListingDate = parseNSEDate(details.Metadata.ListingDate)
	scrip.IndexMembership = []string(details.Metadata.PDSectorIndAll)

	// Flags
	scrip.IsFNO = details.Info.IsFNOSec
	scrip.IsSME = details.SecurityInfo.BoardStatus == "SME"

	// Valuation (partial — market cap comes from trade info)
	scrip.TotalShares = details.SecurityInfo.IssuedSize

	// Ratios
	scrip.PESymbol = float64(details.Metadata.PDSymbolPe)
	scrip.PESector = float64(details.Metadata.PDSectorPe)

	// 2. Trade info (market cap, free float).
	var trade tradeInfoResponse
	if err := c.getJSON("/api/quote-equity?symbol="+encoded+"&section=trade_info", &trade); err != nil {
		return fmt.Errorf("trade info for %s: %w", symbol, err)
	}

	scrip.MarketCap = crToRupees(trade.MarketDeptOrderBook.TradeInfo.TotalMarketCap)
	scrip.FreeFloatMarketCap = crToRupees(trade.MarketDeptOrderBook.TradeInfo.FFMC)

	return nil
}
