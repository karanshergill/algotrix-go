package features

import (
	"context"
	"testing"
)

func TestGetSectorNames(t *testing.T) {
	names := GetSectorNames()
	if len(names) != 12 {
		t.Errorf("expected 12 sectors, got %d", len(names))
	}

	// Verify key sectors exist
	nameSet := make(map[string]bool)
	for _, n := range names {
		nameSet[n] = true
	}
	for _, want := range []string{"NIFTY_BANK", "NIFTY_IT", "NIFTY_FMCG", "NIFTY_PHARMA"} {
		if !nameSet[want] {
			t.Errorf("expected sector %q in list", want)
		}
	}
}

func TestLoadSectorMapping(t *testing.T) {
	stocks := map[string]*StockState{
		"INE001": {ISIN: "INE001", Symbol: "HDFCBANK"},
		"INE002": {ISIN: "INE002", Symbol: "TCS"},
	}
	sectors := make(map[string]*SectorState)

	// nil pool → falls through to static mapping
	loadSectorMapping(context.Background(), nil, stocks, sectors)

	// Should have all sectors from static mapping
	if len(sectors) != len(sectorSymbols) {
		t.Errorf("expected %d sectors, got %d", len(sectorSymbols), len(sectors))
	}

	// Verify sector state is properly initialized
	for name, sec := range sectors {
		if sec.Name != name {
			t.Errorf("sector %q has Name=%q", name, sec.Name)
		}
	}

	// HDFCBANK should be resolved into NIFTY_BANK (and NIFTY_FIN_SVC)
	s := stocks["INE001"]
	if s.SectorID == "" {
		t.Error("HDFCBANK should have a SectorID assigned")
	}
}

func TestLoadSectorMappingStatic_ResolvesSymbols(t *testing.T) {
	stocks := map[string]*StockState{
		"INE001": {ISIN: "INE001", Symbol: "RELIANCE"},
		"INE002": {ISIN: "INE002", Symbol: "TCS"},
		"INE003": {ISIN: "INE003", Symbol: "SBIN"},
	}
	sectors := make(map[string]*SectorState)

	loadSectorMappingStatic(stocks, sectors)

	// RELIANCE should be in NIFTY_ENERGY
	if stocks["INE001"].SectorID != "NIFTY_ENERGY" {
		t.Errorf("RELIANCE SectorID = %q, want NIFTY_ENERGY", stocks["INE001"].SectorID)
	}

	// TCS should be in NIFTY_IT
	if stocks["INE002"].SectorID != "NIFTY_IT" {
		t.Errorf("TCS SectorID = %q, want NIFTY_IT", stocks["INE002"].SectorID)
	}

	// NIFTY_ENERGY should have RELIANCE as member
	energy := sectors["NIFTY_ENERGY"]
	found := false
	for _, isin := range energy.MemberISINs {
		if isin == "INE001" {
			found = true
			break
		}
	}
	if !found {
		t.Error("NIFTY_ENERGY should contain INE001 (RELIANCE)")
	}
}

// TestTimeToSlot lives in registry_test.go (already covers 9:15→0, 9:20→1, 15:25→74)
