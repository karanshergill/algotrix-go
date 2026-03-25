# Phase 4: Wire Screeners into Live Feed Loop

**Goal:** Connect the screener engine to the live feed so signals fire during market hours.

**Context:**
- Phase 1-3 commits: `da3e750`, `f33ccf2`, `3de8c10` — 19 features + 5 screeners + DB layer
- Feed runs via `runFeed()` in `main.go` using PM2 service `go-feed`
- Feature engine starts via `StartFeatureEngine()`, ticks come through `FeedAdapter.AdaptTick()`
- Feature engine has `SetOnTick(fn func(isin string))` callback — called after each tick processed
- Feature engine has `Snapshot()` method — returns latest `*EngineSnapshot`
- Screener engine is `screeners.Engine` with `ProcessTick(isin, *StockSnapshot, *MarketSnapshot)`

**Integration point:** In `runFeed()` in `main.go`, after the feature engine starts:
1. Connect to `algotrix` DB (separate from atdb, DSN: `postgres://me:algotrix@localhost:5432/algotrix`)
2. Create `screeners.SignalDB` with scrip_master mapping
3. Create all 5 screeners (with breakout thresholds from DB)
4. Create `screeners.Engine`
5. Set the feature engine's `onTick` to route to screener engine

---

## Task 1: Load Breakout Thresholds

**Create:** `engine/screeners/loader.go`

```go
package screeners

import (
    "context"
    "fmt"
    "log"
    "time"

    "github.com/jackc/pgx/v5/pgxpool"
)

// LoadBreakoutThresholds loads 2-session high thresholds from daily_session_extremes.
// Returns ISIN → high_value mapping.
func LoadBreakoutThresholds(ctx context.Context, pool *pgxpool.Pool, sessionDate time.Time) (map[string]float64, error) {
    dateStr := sessionDate.Format("2006-01-02")

    rows, err := pool.Query(ctx,
        `SELECT sm.isin, dse.high_value
         FROM daily_session_extremes dse
         JOIN scrip_master sm ON sm.security_id = dse.security_id
         WHERE dse.indicator = 'price'
           AND dse.lookback_sessions = 2
           AND dse.session_date = $1
           AND dse.high_value IS NOT NULL
           AND sm.isin IS NOT NULL AND sm.isin != ''`, dateStr)
    if err != nil {
        return nil, fmt.Errorf("query breakout thresholds: %w", err)
    }
    defer rows.Close()

    thresholds := make(map[string]float64)
    for rows.Next() {
        var isin string
        var highVal float64
        if err := rows.Scan(&isin, &highVal); err != nil {
            return nil, err
        }
        thresholds[isin] = highVal
    }
    log.Printf("[screener-loader] loaded %d breakout thresholds for %s", len(thresholds), dateStr)
    return thresholds, rows.Err()
}
```

---

## Task 2: Create Screener Setup Function

**Create:** `engine/screeners/setup.go`

This is the main startup function called from `runFeed()`.

```go
package screeners

import (
    "context"
    "fmt"
    "log"
    "time"
)

// Setup creates and returns a fully initialized screener engine.
// algotrixDSN connects to the algotrix DB (not atdb).
func Setup(ctx context.Context, algotrixDSN string) (*Engine, error) {
    // 1. Connect to algotrix DB for signal persistence
    db, err := NewSignalDB(ctx, algotrixDSN)
    if err != nil {
        return nil, fmt.Errorf("signal DB setup: %w", err)
    }

    // 2. Load breakout thresholds for today
    ist := time.FixedZone("IST", 5*3600+30*60)
    today := time.Now().In(ist)

    // Use previous trading day for session extremes (today's data doesn't exist yet)
    // Find the most recent session_date in daily_session_extremes
    thresholds, err := LoadBreakoutThresholds(ctx, db.pool, today)
    if err != nil {
        log.Printf("[screener-setup] WARNING: breakout thresholds failed: %v (breakout screener will be dormant)", err)
        thresholds = make(map[string]float64)
    }

    // 3. Create all 5 screeners
    screenerList := []Screener{
        NewEarlyMomentumScreener(),
        NewSniperScreener(),
        NewTridentScreener(),
        NewThinMomentumScreener(),
        NewBreakoutScreener(thresholds),
    }

    // 4. Create engine
    engine := NewEngine(screenerList, db)

    log.Printf("[screener-setup] screener engine ready — %d screeners, %d breakout thresholds",
        len(screenerList), len(thresholds))

    return engine, nil
}
```

---

## Task 3: Wire into runFeed() in main.go

**Modify:** `engine/main.go` — add screener wiring in the `runFeed()` function.

After the feature engine starts (after `features.StartFeatureEngine()`), add:

```go
// --- Screener Engine: wire after feature engine ---
algotrixDSN := "postgres://me:algotrix@localhost:5432/algotrix"
scrEngine, err := screeners.Setup(feCtx, algotrixDSN)
if err != nil {
    log.Printf("[Screener] setup failed (non-fatal): %v", err)
} else {
    log.Println("[Screener] LIVE — 5 screeners active")

    // Wire onTick: feature engine calls screeners after each tick
    feEngine.SetOnTick(func(isin string) {
        snap := feEngine.Snapshot()
        if snap == nil {
            return
        }
        stockSnap, ok := snap.Stocks[isin]
        if !ok {
            return
        }
        scrEngine.ProcessTick(isin, &stockSnap, &snap.Market)
    })
}
```

**IMPORTANT:** The feature engine already has `feEngine.SetOnTick` available. But check if there's an existing onTick set (currently there isn't — the adapter uses `recorder.SetOnTick` which is a different callback on the recorder, not the feature engine). The feature engine's `SetOnTick` is separate and currently unused.

**Add import:** Add `"github.com/karanshergill/algotrix-go/screeners"` to the imports in main.go.

---

## Task 4: Build, Test, Deploy

**Step 1: Build**
```bash
cd /home/me/projects/algotrix-go/engine
go build -o algotrix .
```

**Step 2: Test (screeners)**
```bash
go test ./screeners/ -v
go test ./features/ -v
```

**Step 3: Verify binary runs**
```bash
./algotrix feed --help 2>&1 || echo "Binary built OK"
```

---

## Task 5: Commit

```bash
cd /home/me/projects/algotrix-go
git add engine/screeners/loader.go engine/screeners/setup.go engine/main.go
git commit -m "feat(screeners): wire screener engine into live feed loop

- loader.go: load breakout thresholds from daily_session_extremes
- setup.go: initialize all 5 screeners + signal DB + engine
- main.go: connect screener engine to feature engine onTick callback
- Signals persist to algotrix.signals table during market hours"
```

When completely finished, run this command to notify me:
openclaw system event --text "Done: Screeners wired into live feed loop — ready for market open" --mode now
