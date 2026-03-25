# Screener Port: Python v2 → Go Feature Engine

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Port all 5 screeners from algotrix-v2 (Python/Dhan) to algotrix-go (Go/Fyers), consuming features directly from the Go Feature Engine's in-memory state. Same logic, same thresholds, no new capabilities.

**Architecture:** Screeners live in a new `engine/screeners/` package. They consume `StockSnapshot` + `MarketSnapshot` from the feature engine after each tick. The engine calls screeners via a hook in `handleTick()`. Signals persist to the existing `algotrix.signals` table.

**Tech Stack:** Go, PostgreSQL (`algotrix` DB for signals + `scrip_master`, `atdb` for baselines), YAML configs.

---

## Context: What Exists Today

### Go Feature Engine (`engine/features/`)
- **17 features** already computed: price (5), volume (4), book (3), breadth (3), sector (2)
- **Single-writer event loop** in `engine.go` → `handleTick()` → computes features → updates snapshot
- **Immutable snapshots** (`EngineSnapshot` / `StockSnapshot`) for concurrent reads
- **StockState** has: LTP, DayOpen/High/Low, PrevClose, CumulativeVolume, BuyVol5m/SellVol5m (rolling), VolumeSlot baselines, ATR14d, depth L5, etc.
- **MarketSnapshot** has: NiftyLTP, NiftyPrevClose, NiftyDayHigh/Low, StocksUp/Down, breadth
- **Config:** `engine/features/features.yaml` — windows, baselines, guard thresholds
- **Feed:** Fyers DataSocket, 2,412 stocks, PM2 service `go-feed`

### Python v2 Screeners (`algotrix-v2/src/screeners/`)
5 screeners consuming live indicators + Dhan feed (325 scrips):
1. `EarlyMomentumScreener` — volume spike + buy pressure + price change
2. `SniperScreener` — early momentum + VWAP/exhaustion/book guards
3. `TridentScreener` — sniper + rejection guards (VWAP ceiling, hour reject, spike cap)
4. `ThinMomentumScreener` — relaxed thresholds for small-caps + confirming ticks
5. `TwoSessionHighBreakoutScreener` — N-session high crossover + volume/exhaustion/VWAP confirmation

### Signal Table (already exists in `algotrix` DB)
```sql
-- Table: public.signals
-- Key columns:
--   session_date DATE NOT NULL
--   triggered_at TIMESTAMPTZ NOT NULL
--   screener_name TEXT NOT NULL
--   security_id INTEGER NOT NULL (FK → scrip_master)
--   trading_symbol TEXT NOT NULL
--   signal_type TEXT NOT NULL ('ALERT' | 'BUY' | 'BREAKOUT')
--   trigger_price NUMERIC(16,2) NOT NULL
--   threshold_price NUMERIC(16,2)
--   ltp NUMERIC(16,2) NOT NULL
--   percent_above NUMERIC(8,2)
--   metadata JSONB
--   dedup_key TEXT UNIQUE
```

---

## Gap Analysis: What the Go Engine Is Missing

### Gap 1: Volume Spike Ratio (CRITICAL)

**Problem:** Go engine computes `volume_spike_z` = `(Volume5m - Mean) / StdDev` (z-score). v2 uses bucket ratio = `current_bucket_vol / avg_baseline_vol`. These are fundamentally different scales. Threshold 2.0x ratio ≠ 2.0 z-score.

**Solution:** Add new feature `volume_spike_ratio` to `features_volume.go`.

The Go engine already has `VolumeSlotBaseline.Mean` per 5-min slot loaded from `nse_cm_ticks` in `baselines.go`. The new feature simply divides rolling 5m volume by the slot mean:

```go
// volume_spike_ratio = Volume5m.Sum() / VolumeSlot[currentSlot].Mean
```

**Note on bucket alignment:** v2 tracks volume per discrete 5-min bucket (10:15-10:19:59). Go engine uses a continuous 300s rolling window. At 10:17, v2 has 2 minutes of bucket volume vs Go's 5 minutes of rolling volume. The rolling approach is more stable. The baseline Mean was also computed from similar 5-min aggregations, so the ratio is comparable. Accept this minor behavioral difference.

**Min volume floor:** v2 returns 1.0 (no spike) if baseline < 10,000. Replicate: if `Mean < 10000 || Samples < 5` → return 0 (not ready).

### Gap 2: Classified Volume 5m (CRITICAL)

**Problem:** v2 screeners check `total_classified_volume >= 5000` (buy_vol + sell_vol in the 5m window). Go engine computes `buy_pressure_5m` ratio but does NOT expose the denominator.

**Solution:** Add new feature `classified_volume_5m` to `features_volume.go`:

```go
// classified_volume_5m = BuyVol5m.Sum() + SellVol5m.Sum()
```

Trivial — both rolling sums already exist.

### Gap 3: Tick Classification Method (IMPORTANT)

**Problem:** v2 uses **Quote Rule** (LTP vs L1 bid/ask):
- `LTP >= ask` → BUY
- `LTP <= bid` → SELL
- `LTP > mid` → BUY, `LTP < mid` → SELL
- Fallback: book imbalance (total_buy_qty > total_sell_qty)

Go engine uses **Tick Rule** (uptick/downtick):
- `price > lastPrice` → BUY
- `price < lastPrice` → SELL
- Equal → carry last direction

**Impact:** This affects `buy_pressure_5m` and `classified_volume_5m`. Different classification = different ratios for the same trades.

**Solution:** Update `ClassifyTick()` in `engine.go` to use Quote Rule when depth data is available, falling back to tick rule when no depth.

```go
func (s *StockState) ClassifyTick(price float64, volumeDelta int64, ts time.Time) {
    var isBuy bool

    // Quote Rule (when depth available)
    if s.HasDepth && s.AskPrices[0] > 0 && s.BidPrices[0] > 0 {
        if price >= s.AskPrices[0] {
            isBuy = true
        } else if price <= s.BidPrices[0] {
            isBuy = false
        } else {
            mid := (s.BidPrices[0] + s.AskPrices[0]) / 2
            if price > mid {
                isBuy = true
            } else if price < mid {
                isBuy = false
            } else {
                isBuy = s.TotalBidQty > s.TotalAskQty
            }
        }
    } else {
        // Tick Rule fallback
        if price > s.LastLTP {
            isBuy = true
        } else if price < s.LastLTP {
            isBuy = false
        } else {
            isBuy = s.LastDirection >= 0
        }
    }

    if isBuy {
        s.LastDirection = 1
        s.CumulativeBuyVol += volumeDelta
        s.BuyVol5m.Add(ts, volumeDelta)
    } else {
        s.LastDirection = -1
        s.CumulativeSellVol += volumeDelta
        s.SellVol5m.Add(ts, volumeDelta)
    }
    s.LastLTP = price
}
```

### Gap 4: Market Regime (MINOR)

**Problem:** v2's `MarketRegimeIndicator` checks if Nifty is bullish. Go engine has `breadth_ratio` — different signal.

**Solution:** The screener can compute Nifty regime directly from `MarketSnapshot`:
```go
niftyBullish := snap.Market.NiftyLTP > snap.Market.NiftyPrevClose
```

Implement in screener code, not as an engine feature.

### Gap 5: ISIN → security_id Mapping (for signal persistence)

**Solution:** At startup, load from `algotrix.scrip_master`:
```sql
SELECT isin, security_id, trading_symbol FROM scrip_master WHERE isin IS NOT NULL
```

### Gap 6: Session Extremes for Breakout Screener

**Solution:** At startup, load thresholds via JOIN:
```sql
SELECT sm.isin, dse.high_value
FROM daily_session_extremes dse
JOIN scrip_master sm ON sm.security_id = dse.security_id
WHERE dse.indicator = 'price' AND lookback_sessions = 2 AND session_date = $1
AND dse.high_value IS NOT NULL
```

---

## Implementation Tasks

### Task 1: Add Missing Features to Go Engine

**Files:**
- Modify: `engine/features/features_volume.go`
- Modify: `engine/features/engine.go` (ClassifyTick)
- Test: existing test files + new tests

**Step 1:** Add `volume_spike_ratio` feature to `RegisterVolumeFeatures` in `features_volume.go`:
```go
r.Register(FeatureDef{
    Name: "volume_spike_ratio", Version: 1, Category: "volume",
    Trigger: TriggerTick,
    Ready: func(s *StockState, m *MarketState) bool {
        slot := timeToSlot(s.LastTickTS)
        b, ok := s.VolumeSlot[slot]
        return ok && b.Mean >= 10000 && b.Samples >= 5
    },
    Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
        slot := timeToSlot(s.LastTickTS)
        b := s.VolumeSlot[slot]
        vol5m := float64(s.Volume5m.Sum())
        if vol5m <= 0 { return 0 }
        return vol5m / b.Mean
    },
})
```

**Step 2:** Add `classified_volume_5m` feature:
```go
r.Register(FeatureDef{
    Name: "classified_volume_5m", Version: 1, Category: "volume",
    Trigger: TriggerTick,
    Ready:   func(s *StockState, m *MarketState) bool { return true },
    Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
        return float64(s.BuyVol5m.Sum() + s.SellVol5m.Sum())
    },
})
```

**Step 3:** Replace `ClassifyTick` in `engine.go` with Quote Rule implementation (see Gap 3).

**Step 4:** Write and run tests:
```bash
cd /home/me/projects/algotrix-go && go test ./engine/features/ -v -run "VolumeSpikeRatio|ClassifiedVolume|ClassifyTick"
```

**Step 5:** Commit:
```bash
git add engine/features/features_volume.go engine/features/engine.go
git commit -m "feat(features): add volume_spike_ratio, classified_volume_5m, Quote Rule classification"
```

---

### Task 2: Create Screener Package Structure

**Files:**
- Create: `engine/screeners/screener.go` — interfaces + types
- Create: `engine/screeners/engine.go` — screener engine (routing, dedup, persistence)
- Create: `engine/screeners/config.go` — YAML config loading
- Create: `engine/screeners/db.go` — signal persistence + scrip_master mapping

**Key types:**

```go
// SignalType
type SignalType string
const (
    SignalAlert    SignalType = "ALERT"
    SignalBuy      SignalType = "BUY"
    SignalBreakout SignalType = "BREAKOUT"
)

// Signal
type Signal struct {
    ScreenerName   string
    ISIN           string
    SecurityID     int
    TradingSymbol  string
    SignalType     SignalType
    TriggerPrice   float64
    ThresholdPrice float64
    LTP            float64
    PercentAbove   float64
    TriggeredAt    time.Time
    Metadata       map[string]interface{}
}

// TickContext — everything a screener needs per stock
type TickContext struct {
    ISIN, Symbol string
    LTP, PrevClose, DayOpen, DayHigh, DayLow float64
    Features map[string]float64
    Market   MarketContext
    TickTime time.Time
}

// MarketContext
type MarketContext struct {
    NiftyLTP, NiftyPrevClose, BreadthRatio float64
}

// Screener interface
type Screener interface {
    Name() string
    Evaluate(ctx TickContext, prevLTP float64) *Signal
    Reset()
}
```

**ScreenerEngine:**
- `prevLTP map[string]float64` — "screenerName:ISIN" → last LTP
- `triggeredToday map[string]bool` — dedup set
- Market hours gate: 09:15–15:30
- Day rollover resets all state
- At startup: loads existing signals from DB to pre-populate dedup set (like v2)

**SignalDB:**
- Connects to `algotrix` DB (separate pool)
- Loads ISIN → security_id + trading_symbol from `scrip_master`
- INSERT INTO signals with dedup_key

---

### Task 3: Port EarlyMomentum Screener

**File:** `engine/screeners/early_momentum.go`

**Exact thresholds (DO NOT CHANGE):**
- min_spike_ratio: 2.0
- min_buy_ratio: 0.65
- min_total_volume: 5000
- min_change_pct: 0.5
- max_change_pct: 3.0
- Signal type: ALERT

**Filter chain (ALL must pass):**
1. PrevClose > 0
2. `features["change_pct"]` between 0.5 and 3.0
3. `features["volume_spike_ratio"]` >= 2.0
4. `features["buy_pressure_5m"]` >= 0.65
5. `features["classified_volume_5m"]` >= 5000
6. Not already signaled today

---

### Task 4: Port Sniper Screener

**File:** `engine/screeners/sniper.go`

**Exact thresholds (DO NOT CHANGE):**
- All EarlyMomentum thresholds PLUS:
- min_gap_pct: 0.1, max_gap_pct: 3.0
- min_signal_time: "10:00" IST
- max_exhaustion: 0.5
- min_book_imbalance: 0.55
- require_above_vwap: true
- Signal type: BUY

**Filter chain:**
1. PrevClose > 0
2. `features["change_pct"]` between 0.1 and 3.0
3. Time >= 10:00 AM IST
4. `features["volume_spike_ratio"]` >= 2.0
5. `features["buy_pressure_5m"]` >= 0.65
6. `features["classified_volume_5m"]` >= 5000
7. `features["vwap_dist_bps"]` > 0 (above VWAP)
8. `features["exhaustion"]` < 0.5
9. `features["book_imbalance"]` >= 0.55
10. Not already signaled today

---

### Task 5: Port Trident Screener

**File:** `engine/screeners/trident.go`

**Exact thresholds (DO NOT CHANGE):**
- All Sniper thresholds PLUS:
- max_vwap_ratio: 1.015 → `features["vwap_dist_bps"]` <= 150
- reject_hours: [11]
- max_spike_ratio: 5.0
- Signal type: BUY
- Exit params in metadata: target 1%, stop 2%, trail 0.3%/0.35%, sqoff 15:20

---

### Task 6: Port ThinMomentum Screener

**File:** `engine/screeners/thin_momentum.go`

**Exact thresholds (DO NOT CHANGE):**
- min_spike_ratio: 1.5, min_buy_ratio: 0.60, min_total_volume: 500
- min_change_pct: 0.5, max_change_pct: 3.0
- min_book_buy_ratio: 0.60, min_price: 100, max_price: 2000
- min_confirming_ticks: 3, warmup_ticks: 20
- Signal type: ALERT

**Unique: confirming tick counter + warmup + spike-OR-book override**
- Track `confirmingTicks[ISIN]` — reset to 0 on any fail, fire on >= 3
- Track `tickCount[ISIN]` — skip until >= 20
- Spike check: `spike >= 1.5 OR book_imbalance >= 0.70`

---

### Task 7: Port TwoSessionHighBreakout Screener

**File:** `engine/screeners/breakout.go`

**Exact thresholds (DO NOT CHANGE):**
- lookback_sessions: 2, first_break_only: true
- require_volume_spike: 1.5, max_exhaustion: 0.75
- require_above_vwap: true, max_rejection_wick_pct: 2.0
- Signal type: BREAKOUT

**Unique: crossover detection**
- Fire ONLY when prevLTP <= threshold AND currentLTP > threshold
- Uses prevLTP from screener engine (not first tick)

**Startup: load thresholds from DB + rejection wick pre-filter**

---

### Task 8: Wire Screeners into Feed Loop

**Files:**
- Modify: `engine/features/engine.go` — add ScreenerEngine field + setter + call in handleTick
- Modify: feed command startup — instantiate screener engine
- Create: `engine/screeners/screeners.yaml` — default config with all thresholds

**Integration in handleTick():** After existing onTick callback, call `screenerEngine.ProcessTick(isin, stockSnap, marketSnap)`

**Startup sequence:**
1. Connect to algotrix DB (separate pool)
2. Load scrip_master mapping
3. Load session extremes for breakout
4. Load screener YAML config
5. Create all 5 screeners
6. Create ScreenerEngine
7. engine.SetScreenerEngine(se)

---

### Task 9: Recompute Session Extremes

Run v2 batch job to refresh `daily_session_extremes` for tomorrow:
```bash
cd /home/me/projects/algotrix-v2 && source .venv/bin/activate && python -m src.indicators.session_extremes
```

---

### Task 10: Build, Test, Deploy

```bash
cd /home/me/projects/algotrix-go
go build ./engine/...
go test ./engine/features/ -v
go test ./engine/screeners/ -v
pm2 restart go-feed
# Verify:
PGPASSWORD=algotrix psql -h localhost -U me -d algotrix \
  -c "SELECT screener_name, trading_symbol, signal_type, ltp, triggered_at FROM signals WHERE session_date = CURRENT_DATE ORDER BY triggered_at DESC LIMIT 10"
```

---

## Task Dependencies

```
Task 1 (features) → Task 2 (package) → Tasks 3-7 (screeners, parallel) → Task 8 (wiring) → Task 9 (session extremes) → Task 10 (deploy)
```

## Risk Register

1. **Volume spike ratio** — Rolling 5m vs discrete bucket. Accept as improvement.
2. **Quote Rule change** — Will shift buy_pressure values vs tick rule. Monitor.
3. **Session extremes stale** — Must recompute. If fails, breakout screener is dormant.
4. **ISIN coverage** — Some ISINs may not be in scrip_master. Log and skip.
5. **Dual DB connection** — Need second pool for algotrix DB.
