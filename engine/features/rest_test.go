package features

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func setupRESTEngine(t *testing.T) (*FeatureEngine, *httptest.Server) {
	t.Helper()
	e := NewFeatureEngine(DefaultEngineConfig())
	e.RegisterStock("INE001", "RELIANCE", "NIFTY_BANK")
	e.RegisterStock("INE002", "TCS", "NIFTY_IT")
	e.RegisterSector("NIFTY_BANK", []string{"INE001"})
	e.RegisterSector("NIFTY_IT", []string{"INE002"})
	e.Session().SessionStart(time.Now())

	done := make(chan string, 16)
	e.SetOnTick(func(isin string) { done <- isin })

	ctx, cancel := context.WithCancel(context.Background())
	go e.Run(ctx)

	ts := time.Now()
	e.TickChan() <- TickEvent{ISIN: "INE001", Symbol: "RELIANCE", LTP: 2500.0, Volume: 1000, TS: ts}
	waitFor(t, done, 2*time.Second)
	e.TickChan() <- TickEvent{ISIN: "INE002", Symbol: "TCS", LTP: 3400.0, Volume: 500, TS: ts}
	waitFor(t, done, 2*time.Second)

	cancel()

	rest := NewRESTServer(e, 0)
	srv := httptest.NewServer(rest.Handler())
	t.Cleanup(srv.Close)
	return e, srv
}

func TestRESTServer_AllFeatures(t *testing.T) {
	_, srv := setupRESTEngine(t)

	resp, err := http.Get(srv.URL + "/features")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); ct != "application/json" {
		t.Fatalf("Content-Type = %q, want application/json", ct)
	}

	var result map[string]StockSnapshot
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}

	if len(result) != 2 {
		t.Fatalf("got %d stocks, want 2", len(result))
	}
	if result["INE001"].Symbol != "RELIANCE" {
		t.Errorf("INE001 symbol = %q, want RELIANCE", result["INE001"].Symbol)
	}
	if result["INE002"].LTP != 3400.0 {
		t.Errorf("INE002 LTP = %f, want 3400.0", result["INE002"].LTP)
	}
}

func TestRESTServer_SingleStock(t *testing.T) {
	_, srv := setupRESTEngine(t)

	resp, err := http.Get(srv.URL + "/features/INE001")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	var stock StockSnapshot
	if err := json.NewDecoder(resp.Body).Decode(&stock); err != nil {
		t.Fatal(err)
	}

	if stock.ISIN != "INE001" {
		t.Errorf("ISIN = %q, want INE001", stock.ISIN)
	}
	if stock.Symbol != "RELIANCE" {
		t.Errorf("Symbol = %q, want RELIANCE", stock.Symbol)
	}
	if stock.LTP != 2500.0 {
		t.Errorf("LTP = %f, want 2500.0", stock.LTP)
	}
}

func TestRESTServer_SingleStock_NotFound(t *testing.T) {
	_, srv := setupRESTEngine(t)

	resp, err := http.Get(srv.URL + "/features/FAKE")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 404 {
		t.Fatalf("status = %d, want 404", resp.StatusCode)
	}
}

func TestRESTServer_Market(t *testing.T) {
	_, srv := setupRESTEngine(t)

	resp, err := http.Get(srv.URL + "/features/market")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	var market MarketSnapshot
	if err := json.NewDecoder(resp.Body).Decode(&market); err != nil {
		t.Fatal(err)
	}

	if market.TotalStocks != 2 {
		t.Errorf("TotalStocks = %d, want 2", market.TotalStocks)
	}
}

func TestRESTServer_Sector(t *testing.T) {
	_, srv := setupRESTEngine(t)

	resp, err := http.Get(srv.URL + "/features/sector/NIFTY_BANK")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}

	var sector SectorSnapshot
	if err := json.NewDecoder(resp.Body).Decode(&sector); err != nil {
		t.Fatal(err)
	}

	if sector.Name != "NIFTY_BANK" {
		t.Errorf("Name = %q, want NIFTY_BANK", sector.Name)
	}
	if sector.TotalStocks != 1 {
		t.Errorf("TotalStocks = %d, want 1", sector.TotalStocks)
	}

	// Also test 404 for unknown sector
	resp2, err := http.Get(srv.URL + "/features/sector/FAKE_SECTOR")
	if err != nil {
		t.Fatal(err)
	}
	defer resp2.Body.Close()
	if resp2.StatusCode != 404 {
		t.Fatalf("unknown sector status = %d, want 404", resp2.StatusCode)
	}
}
