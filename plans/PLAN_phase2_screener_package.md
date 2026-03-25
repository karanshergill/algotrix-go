# Phase 2: Screener Package + EarlyMomentum Screener

**Goal:** Build the screener package structure and port the simplest screener (EarlyMomentum) as proof of concept. Once this works end-to-end (tick → evaluate → signal → DB), adding the other 4 screeners is straightforward.

**Constraint:** Same logic, same thresholds as v2. No improvements.

---

## Context

Phase 1 (commit `da3e750`) added 2 features to the Go engine:
- `volume_spike_ratio` — slot volume / historical baseline mean
- `classified_volume_5m` — total classified buy+sell in 5-min rolling window

The screeners consume these features (+ existing ones) via `StockSnapshot.Features` map.

**StockSnapshot** has: ISIN, Symbol, LTP, Features map
**MarketSnapshot** has: NiftyLTP, NiftyPrevClose, StocksUp/Down, breadth data

---

## Task 1: Create Screener Package — Types & Interfaces

**Create:** `engine/screeners/types.go`

```go
package screeners

import "time"

// SignalType represents the kind of signal.
type SignalType string

const (
    SignalAlert    SignalType = "ALERT"
    SignalBuy      SignalType = "BUY"
    SignalBreakout SignalType = "BREAKOUT"
)

// Signal represents a screener output.
type Signal struct {
    ScreenerName   string
    ISIN           string
    Symbol         string
    SignalType     SignalType
    LTP            float64
    TriggerPrice   float64   // price at signal time
    ThresholdPrice float64   // reference (prev close, N-session high, etc.)
    PercentAbove   float64   // % above threshold
    TriggeredAt    time.Time
    Metadata       map[string]interface{}
}

// Screener interface — each screener implements this.
type Screener interface {
    Name() string
    Evaluate(ctx *TickContext) *Signal
    Reset() // call on day rollover
}

// TickContext holds everything a screener needs to evaluate one stock.
type TickContext struct {
    ISIN     string
    Symbol   string
    LTP      float64
    Features map[string]float64 // from StockSnapshot.Features
    Market   MarketContext
    TickTime time.Time
    PrevLTP  float64 // previous LTP for this screener+ISIN (0 = first tick)
}

// MarketContext holds market-level data.
type MarketContext struct {
    NiftyLTP       float64
    NiftyPrevClose float64
}
```

---

## Task 2: Create Screener Engine

**Create:** `engine/screeners/engine.go`

The engine routes ticks to all screeners, handles dedup (one signal per screener per stock per day), tracks prevLTP, and manages day rollover.

```go
package screeners

import (
    "log"
    "time"

    "algotrix/engine/features"
)

// Engine manages all screeners and routes ticks to them.
type Engine struct {
    screeners      []Screener
    prevLTP        map[string]float64  // "screenerName:ISIN" → last LTP
    triggeredToday map[string]bool     // "screenerName:ISIN" → already fired
    sessionDate    string              // "2006-01-02" for day rollover detection
    db             *SignalDB           // nil = no persistence (testing)
}

// NewEngine creates a screener engine.
func NewEngine(screeners []Screener, db *SignalDB) *Engine {
    return &Engine{
        screeners:      screeners,
        prevLTP:        make(map[string]float64),
        triggeredToday: make(map[string]bool),
        db:             db,
    }
}

// ProcessTick evaluates all screeners for one stock tick.
// Call this from the feature engine's onTick callback.
func (e *Engine) ProcessTick(isin string, stockSnap *features.StockSnapshot, marketSnap *features.MarketSnapshot) []*Signal {
    now := time.Now()
    ist := now.In(time.FixedZone("IST", 5*3600+30*60))

    // Day rollover check
    today := ist.Format("2006-01-02")
    if e.sessionDate != "" && today != e.sessionDate {
        e.resetDay()
    }
    e.sessionDate = today

    // Market hours gate: 09:15 - 15:30 IST
    hour, min := ist.Hour(), ist.Minute()
    marketMinute := hour*60 + min
    if marketMinute < 9*60+15 || marketMinute > 15*60+30 {
        return nil
    }

    var signals []*Signal

    mctx := MarketContext{
        NiftyLTP:       marketSnap.NiftyLTP,
        NiftyPrevClose: marketSnap.NiftyPrevClose,
    }

    for _, scr := range e.screeners {
        key := scr.Name() + ":" + isin

        // Dedup: skip if already signaled today
        if e.triggeredToday[key] {
            continue
        }

        prevLTP := e.prevLTP[key]
        e.prevLTP[key] = stockSnap.LTP

        ctx := &TickContext{
            ISIN:     isin,
            Symbol:   stockSnap.Symbol,
            LTP:      stockSnap.LTP,
            Features: stockSnap.Features,
            Market:   mctx,
            TickTime: ist,
            PrevLTP:  prevLTP,
        }

        sig := scr.Evaluate(ctx)
        if sig != nil {
            sig.TriggeredAt = ist
            sig.ISIN = isin
            sig.Symbol = stockSnap.Symbol
            sig.LTP = stockSnap.LTP
            sig.TriggerPrice = stockSnap.LTP

            e.triggeredToday[key] = true
            signals = append(signals, sig)

            log.Printf("[screener] %s SIGNAL: %s %s @ %.2f (%s)",
                sig.ScreenerName, sig.SignalType, sig.Symbol, sig.LTP, sig.ISIN)

            // Persist to DB
            if e.db != nil {
                if err := e.db.PersistSignal(sig, today); err != nil {
                    log.Printf("[screener] DB persist error: %v", err)
                }
            }
        }
    }

    return signals
}

func (e *Engine) resetDay() {
    log.Println("[screener] Day rollover — resetting all screeners")
    e.prevLTP = make(map[string]float64)
    e.triggeredToday = make(map[string]bool)
    for _, scr := range e.screeners {
        scr.Reset()
    }
}
```

---

## Task 3: Create Signal DB Layer

**Create:** `engine/screeners/db.go`

Connects to `algotrix` DB (NOT atdb). Loads ISIN→security_id mapping from scrip_master. Persists signals.

```go
package screeners

import (
    "context"
    "fmt"
    "log"

    "github.com/jackc/pgx/v5/pgxpool"
)

type SignalDB struct {
    pool      *pgxpool.Pool
    isinToSID map[string]int    // ISIN → security_id
    isinToSym map[string]string // ISIN → trading_symbol
}

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
    log.Printf("[screener-db] Loaded %d ISIN→security_id mappings", len(db.isinToSID))

    return db, nil
}

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

func (db *SignalDB) Close() {
    db.pool.Close()
}
```

---

## Task 4: Port EarlyMomentum Screener

**Create:** `engine/screeners/early_momentum.go`

**Exact v2 thresholds (DO NOT CHANGE):**
- min_spike_ratio: 2.0
- min_buy_ratio: 0.65
- min_total_volume: 5000
- min_change_pct: 0.5
- max_change_pct: 3.0
- Signal type: ALERT

```go
package screeners

// EarlyMomentumScreener detects early momentum via volume spike + buy pressure.
type EarlyMomentumScreener struct {
    MinSpikeRatio  float64
    MinBuyRatio    float64
    MinTotalVolume float64
    MinChangePct   float64
    MaxChangePct   float64
}

func NewEarlyMomentumScreener() *EarlyMomentumScreener {
    return &EarlyMomentumScreener{
        MinSpikeRatio:  2.0,
        MinBuyRatio:    0.65,
        MinTotalVolume: 5000,
        MinChangePct:   0.5,
        MaxChangePct:   3.0,
    }
}

func (s *EarlyMomentumScreener) Name() string { return "early_momentum" }

func (s *EarlyMomentumScreener) Evaluate(ctx *TickContext) *Signal {
    f := ctx.Features
    if f == nil {
        return nil
    }

    // Filter 1: change_pct in range
    changePct, ok := f["change_pct"]
    if !ok || changePct < s.MinChangePct || changePct > s.MaxChangePct {
        return nil
    }

    // Filter 2: volume spike ratio
    spikeRatio, ok := f["volume_spike_ratio"]
    if !ok || spikeRatio < s.MinSpikeRatio {
        return nil
    }

    // Filter 3: buy pressure (5m rolling)
    buyRatio, ok := f["buy_pressure_5m"]
    if !ok || buyRatio < s.MinBuyRatio {
        return nil
    }

    // Filter 4: classified volume minimum
    classVol, ok := f["classified_volume_5m"]
    if !ok || classVol < s.MinTotalVolume {
        return nil
    }

    return &Signal{
        ScreenerName:   s.Name(),
        SignalType:     SignalAlert,
        PercentAbove:   changePct,
        ThresholdPrice: ctx.LTP / (1 + changePct/100), // approximate prev close
        Metadata: map[string]interface{}{
            "screener":           s.Name(),
            "volume_spike_ratio": spikeRatio,
            "buy_ratio":          buyRatio,
            "classified_volume":  classVol,
            "change_pct":         changePct,
        },
    }
}

func (s *EarlyMomentumScreener) Reset() {}
```

---

## Task 5: Write Tests

**Create:** `engine/screeners/early_momentum_test.go`

```go
func TestEarlyMomentumAllPass(t *testing.T) {
    // All conditions met → signal fires
}

func TestEarlyMomentumLowVolume(t *testing.T) {
    // classified_volume < 5000 → no signal
}

func TestEarlyMomentumChangePctOutOfRange(t *testing.T) {
    // change_pct > 3.0 → no signal
    // change_pct < 0.5 → no signal
}

func TestEarlyMomentumLowSpike(t *testing.T) {
    // volume_spike_ratio < 2.0 → no signal
}

func TestEngineDedup(t *testing.T) {
    // Same stock fires twice → only first signal returned
}
```

---

## Task 6: Build and Test

```bash
cd /home/me/projects/algotrix-go
go build ./engine/screeners/...
go test ./engine/screeners/ -v
```

---

## Task 7: Commit

```bash
cd /home/me/projects/algotrix-go
git add engine/screeners/
git commit -m "feat(screeners): package structure + EarlyMomentum screener

- types.go: Signal, Screener interface, TickContext
- engine.go: screener engine with dedup, market hours gate, day rollover
- db.go: signal persistence to algotrix.signals + scrip_master mapping
- early_momentum.go: first screener ported from v2 (same thresholds)
- Tests for all conditions + dedup"
```

When completely finished, run this command to notify me:
openclaw system event --text "Done: Screener package + EarlyMomentum screener built" --mode now
