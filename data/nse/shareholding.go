package nse

import (
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"github.com/karanshergill/algotrix-go/models"
)

// shareholdingRecord represents one quarterly filing from the
// /api/corporate-share-holdings-master endpoint.
type shareholdingRecord struct {
	Date       string `json:"date"`
	PrAndPrGrp string `json:"pr_and_prgrp"`
	PublicVal  string `json:"public_val"`
	XBRL       string `json:"xbrl"`
}

// FetchShareholding fetches the latest shareholding data for a symbol.
// Populates promoter/public from the summary API, then downloads and
// parses the XBRL XML for FII, DII, MF, insurance, retail, pledge, and PSU.
func (c *Client) FetchShareholding(symbol string, scrip *models.Scrip) error {
	encoded := url.QueryEscape(symbol)

	// 1. Fetch shareholding summary.
	var records []shareholdingRecord
	if err := c.getJSON("/api/corporate-share-holdings-master?index=equities&symbol="+encoded, &records); err != nil {
		return fmt.Errorf("shareholding for %s: %w", symbol, err)
	}
	if len(records) == 0 {
		return nil // no shareholding data available
	}

	latest := records[0]

	// Promoter and public from summary.
	scrip.PromoterPct, _ = strconv.ParseFloat(strings.TrimSpace(latest.PrAndPrGrp), 64)
	scrip.PublicPct, _ = strconv.ParseFloat(strings.TrimSpace(latest.PublicVal), 64)
	scrip.ShareholdingQuarter = formatQuarter(latest.Date)

	// 2. Download and parse XBRL if URL is available.
	if latest.XBRL != "" {
		if err := c.parseXBRL(latest.XBRL, scrip); err != nil {
			// Non-fatal: we still have promoter/public from summary.
			fmt.Printf("WARN: XBRL parse failed for %s: %v\n", symbol, err)
		}
	}

	return nil
}

// formatQuarter converts "31-DEC-2025" to "Dec-2025".
func formatQuarter(date string) string {
	parts := strings.Split(date, "-")
	if len(parts) != 3 {
		return date
	}
	month := strings.ToLower(parts[1])
	month = strings.ToUpper(month[:1]) + month[1:]
	return month + "-" + parts[2]
}

// parseXBRL downloads an XBRL XML file and extracts shareholding percentages.
func (c *Client) parseXBRL(xbrlURL string, scrip *models.Scrip) error {
	req, err := http.NewRequest("GET", xbrlURL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", userAgent)

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("downloading XBRL: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("XBRL download: status %d", resp.StatusCode)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}

	return extractFromXBRL(body, scrip)
}

// extractFromXBRL parses XBRL XML bytes and populates shareholding fields.
// Uses regex to extract percentage values by context ID pattern.
func extractFromXBRL(data []byte, scrip *models.Scrip) error {
	content := string(data)

	// Pattern: contextRef="<Category>_ContextI" with a fractional value (0.xxxx).
	// ContextI = summary-level data for the filing period.
	re := regexp.MustCompile(`contextRef="([^"]+_ContextI)"\s*[^>]*>(\d+\.?\d*)`)
	matches := re.FindAllStringSubmatch(content, -1)

	// Collect unique category → percentage mappings.
	// Values in XBRL are fractions (e.g., 0.1376 = 13.76%).
	seen := make(map[string]float64)
	for _, m := range matches {
		ctx := m[1]
		val, err := strconv.ParseFloat(m[2], 64)
		if err != nil || val <= 0 || val > 1 {
			continue
		}
		category := strings.TrimSuffix(ctx, "_ContextI")
		if _, exists := seen[category]; !exists {
			seen[category] = val * 100
		}
	}

	// Map categories to scrip fields.
	if v, ok := seen["InstitutionsForeign"]; ok {
		scrip.FIIPct = v
	}
	if v, ok := seen["InstitutionsDomestic"]; ok {
		scrip.DIIPct = v
	}
	if v, ok := seen["MutualFundsOrUTI"]; ok {
		scrip.MutualFundPct = v
	}
	if v, ok := seen["InsuranceCompanies"]; ok {
		scrip.InsurancePct = v
	}
	if v, ok := seen["NonInstitutions"]; ok {
		scrip.RetailPct = v
	}

	// PSU detection: Central/State Government as promoter.
	if _, ok := seen["CentralGovernmentOrStateGovernmentS"]; ok {
		scrip.IsPSU = true
	}

	// Pledge detection: check for any pledge-related content.
	pledgeRe := regexp.MustCompile(`(?i)SharesPledgedOrOtherwiseEncumbered[^"]*_ContextI"\s*[^>]*>(\d+\.?\d*)`)
	pledgeMatches := pledgeRe.FindAllStringSubmatch(content, -1)
	for _, m := range pledgeMatches {
		val, err := strconv.ParseFloat(m[1], 64)
		if err == nil && val > 0 {
			scrip.PromoterPledged = true
			break
		}
	}

	return nil
}
