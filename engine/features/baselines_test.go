package features

import (
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
		"INE001": {ISIN: "INE001"},
		"INE002": {ISIN: "INE002"},
	}
	sectors := make(map[string]*SectorState)

	loadSectorMapping(stocks, sectors)

	// Should have all sectors from static mapping
	if len(sectors) != len(sectorMembers) {
		t.Errorf("expected %d sectors, got %d", len(sectorMembers), len(sectors))
	}

	// Verify sector state is properly initialized
	for name, sec := range sectors {
		if sec.Name != name {
			t.Errorf("sector %q has Name=%q", name, sec.Name)
		}
	}
}

// TestTimeToSlot lives in registry_test.go (already covers 9:15→0, 9:20→1, 15:25→74)
