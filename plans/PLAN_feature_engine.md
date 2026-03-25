# Plan: Go Feature Engine — Real-Time Market Feature Platform

**Status:** APPROVED — ready for Coder
**Date:** 2026-03-23
**Owner:** Gxozt (plan) → Coder (implementation)
**Reviewed by:** Codex (6 major fixes incorporated), Gemx (async logging adopted), Ricky (tick-level logging + universal naming)

---

## Foundational Decisions

### Universal Naming Convention

**One entity, two names. Nothing else.**

| Name | Purpose | Example |
|------|---------|---------|
| `isin` | Primary key everywhere — code, DB, maps, hub messages | `INE002A01018` |
| `symbol` | Display name — logs, UI, human-readable output | `RELIANCE` |

**Killed terminology:**
- ~~`security_id`~~ → Dhan-specific, we're off Dhan
- ~~`scrip`~~ → NSE jargon, replaced by "stock" in conversation
- ~~`instrument`~~ → Fyers internal, mapped at feed boundary only
- ~~`trading_symbol`~~ → just `symbol`

**Feed boundary mapping:** Fyers uses `NSE:RELIANCE-EQ`. We map to ISIN + Symbol at ingestion. No Fyers naming leaks downstream.

**In code:** `StockState`, not `ScripState` or `InstrumentState`.
**In DB:** All tables use `isin` as key column + `symbol` as denormalized display column.
**In hub messages:** `{"isin": "INE002A01018", "symbol": "RELIANCE", ...}`

### Tick-Level Feature Logging (Default)

Every tick produces a full feature vector. Every vector gets logged to atdb asynchronously.

**Not periodic snapshots. Not sampled. Every tick.**

This gives us:
- Complete time-series of every feature for every stock at every moment
- Trivial backtesting: `SELECT * FROM feature_vectors WHERE isin = '...' AND ts BETWEEN ...`
- ML training data: historical feature vectors as training input
- No data loss if Go engine restarts (everything persisted)

**Write strategy:** Non-blocking channel → dedicated goroutine → bulk INSERT every 500ms or 10K rows (whichever comes first). Live feature computation is never blocked by DB writes.

---

## Overview

Replace the v2 Python indicator system with a Go-native feature engine inside the existing `algotrix-go/engine`. The engine maintains shared state per stock, computes features from primitives, and broadcasts enriched feature vectors via the existing Hub WebSocket + stores them in atdb.

**Principle:** Compute primitives once, derive features from shared state, never duplicate work.

---

## Architecture

```
Fyers Feed (DataSocket + TBT)
       ↓
  Go Engine Process
  ┌──────────────────────────────────────────────┐
  │  Feed Layer (existing)                        │
  │    → tick events, depth events                │
  │    → maps Fyers symbols → ISIN + Symbol       │
  │                                               │
  │  State Layer (NEW)                            │
  │    → StockState per ISIN                      │
  │    → MarketState (cross-stock, delta-updated) │
  │    → Primitives updated on tick               │
  │                                               │
  │  Feature Layer (NEW)                          │
  │    → Feature functions read from state        │
  │    → Feature registry (plugin architecture)   │
  │    → Warmup/readiness tracking per feature    │
  │                                               │
  │  Output Layer                                 │
  │    → Hub WS broadcast (enriched tick+features)│
  │    → atdb raw tick write (existing)           │
  │    → atdb feature vector write (NEW, async)   │
  │    → REST /features endpoint (NEW)            │
  └──────────────────────────────────────────────┘
       ↓                    ↓
  Hub (ws://127.0.0.1:3002)  atdb
       ↓
  Python Screeners (future, Phase 2)
  Hono API → Dashboard
```

---

## Phase 1: State Layer — Primitives

### 1.1 StockState Struct

One instance per active ISIN. Updated on every tick/depth event.

```go
package features

import "time"

// StockState holds all real-time primitives for a single stock.
// Updated by the feed layer on every tick and depth event.
// Keyed by ISIN. Symbol is display-only.
type StockState struct {
    ISIN   string // Primary key (e.g. "INE002A01018")
    Symbol string // Display name (e.g. "RELIANCE")

    // === Tick-Updated Primitives ===

    // Price
    LTP        float64   // Last traded price
    DayOpen    float64   // First trade of the day (set once)
    DayHigh    float64   // Running intraday high
    DayLow     float64   // Running intraday low
    PrevClose  float64   // Previous session close (pre-loaded from atdb)
    LastTickTS time.Time // Timestamp of most recent tick

    // Volume (all cumulative for the day)
    CumulativeVolume   int64   // Total shares traded today
    CumulativeTurnover float64 // Total ₹ value traded today (Σ price × vol_delta)
    CumulativeBuyVol   int64   // Buy-classified volume (tick rule)
    CumulativeSellVol  int64   // Sell-classified volume
    UpdateCount        int64   // Number of feed updates received (NOT trade count)
    // NOTE: Fyers sends cumulative volume per update, not individual trade prints.
    // UpdateCount counts feed events, not actual exchange trades.
    // Do NOT use for "trades per minute" or "avg trade size" — those need true execution data.

    // Tick classification state
    LastDirection int8    // +1 = uptick, -1 = downtick, 0 = unchanged
    LastLTP       float64 // Previous LTP (for tick rule)

    // === Depth-Updated Primitives ===

    BidPrices   [5]float64 // Top 5 bid prices (0 = best)
    BidQtys     [5]int64   // Quantities at each bid level
    AskPrices   [5]float64 // Top 5 ask prices (0 = best)
    AskQtys     [5]int64   // Quantities at each ask level
    TotalBidQty int64      // Sum of all visible bid quantity
    TotalAskQty int64      // Sum of all visible ask quantity

    // === Rolling Window Primitives ===

    Volume1m  *RollingSum     // Volume in last 60 seconds
    Volume5m  *RollingSum     // Volume in last 5 minutes
    BuyVol5m  *RollingSum     // Buy-classified volume, 5-min window
    SellVol5m *RollingSum     // Sell-classified volume, 5-min window
    Updates1m *RollingSum     // Feed updates in last 60 seconds (activity proxy)
    High5m    *RollingExtreme // 5-minute rolling high
    Low5m     *RollingExtreme // 5-minute rolling low

    // === Pre-Loaded Baselines (from atdb at session start) ===

    ATR14d         float64                    // 14-day average true range
    AvgDailyVolume int64                      // 10-day average daily volume
    VolumeSlot     map[int]VolumeSlotBaseline // Per 5-min slot: mean + stddev
    // Slot key: (hour*60 + minute - 555) / 5 → 0, 1, 2, ... 74
    // 555 = 9*60+15 (market open in minutes)

    // === MarketState Delta Tracking ===
    // Track this stock's previous contribution to MarketState
    // so we can do correct incremental updates (remove old, add new)
    prevWasUp        bool // was this stock counted in StocksUp last tick?
    prevWasDown      bool
    prevWasAboveVWAP bool
    prevBuyVol       int64   // last CumulativeBuyVol contributed to market totals
    prevSellVol      int64
    prevVolume       int64
    prevTurnover     float64
}

// VolumeSlotBaseline holds precomputed per-slot volume statistics.
// Computed from last N trading days (not calendar days).
type VolumeSlotBaseline struct {
    Mean    float64
    StdDev  float64
    Samples int // number of days used to compute
}
```

### 1.2 MarketState Struct

Aggregated across all stocks. **Delta-updated** — each stock tracks its previous contribution and removes it before adding the new one.

```go
// MarketState holds cross-sectional market primitives.
// IMPORTANT: Updated via delta method — never recompute from scratch.
// Each StockState tracks its previous contribution (prevWasUp, prevBuyVol, etc.)
// and the FeatureEngine does: remove old contribution → add new contribution.
type MarketState struct {
    TotalStocks     int
    StocksUp        int     // LTP > PrevClose
    StocksDown      int     // LTP < PrevClose
    StocksFlat      int     // LTP == PrevClose (or no tick yet)
    StocksAboveVWAP int     // LTP > stock's VWAP

    TotalMarketBuyVol   int64   // Sum of CumulativeBuyVol across all stocks
    TotalMarketSellVol  int64   // Sum of CumulativeSellVol across all stocks
    TotalMarketVolume   int64   // Sum of CumulativeVolume across all stocks
    TotalMarketTurnover float64

    // Nifty 50 specific (index tracking)
    NiftyLTP       float64
    NiftyPrevClose float64
    NiftyDayHigh   float64
    NiftyDayLow    float64
}

// UpdateMarketState does a delta-update: removes old contribution, adds new.
// Called by FeatureEngine after each StockState update.
func (m *MarketState) UpdateFromStock(s *StockState, vwap float64) {
    // Remove old contribution
    if s.prevWasUp { m.StocksUp-- }
    if s.prevWasDown { m.StocksDown-- }
    if !s.prevWasUp && !s.prevWasDown { m.StocksFlat-- }
    if s.prevWasAboveVWAP { m.StocksAboveVWAP-- }
    m.TotalMarketBuyVol -= s.prevBuyVol
    m.TotalMarketSellVol -= s.prevSellVol
    m.TotalMarketVolume -= s.prevVolume
    m.TotalMarketTurnover -= s.prevTurnover

    // Compute new state
    isUp := s.LTP > s.PrevClose && s.PrevClose > 0
    isDown := s.LTP < s.PrevClose && s.PrevClose > 0
    isAboveVWAP := vwap > 0 && s.LTP > vwap

    // Add new contribution
    if isUp { m.StocksUp++ }
    if isDown { m.StocksDown++ }
    if !isUp && !isDown { m.StocksFlat++ }
    if isAboveVWAP { m.StocksAboveVWAP++ }
    m.TotalMarketBuyVol += s.CumulativeBuyVol
    m.TotalMarketSellVol += s.CumulativeSellVol
    m.TotalMarketVolume += s.CumulativeVolume
    m.TotalMarketTurnover += s.CumulativeTurnover

    // Save for next delta
    s.prevWasUp = isUp
    s.prevWasDown = isDown
    s.prevWasAboveVWAP = isAboveVWAP
    s.prevBuyVol = s.CumulativeBuyVol
    s.prevSellVol = s.CumulativeSellVol
    s.prevVolume = s.CumulativeVolume
    s.prevTurnover = s.CumulativeTurnover
}
```

### 1.3 RollingSum / RollingExtreme

Efficient O(1) rolling window using circular buffer with timestamps:

```go
// RollingSum maintains a time-windowed sum.
// Uses a circular buffer of (timestamp, value) entries.
// On each Add(), evicts entries older than the window, then appends.
type RollingSum struct {
    Window  time.Duration
    entries []tsEntry // circular buffer
    sum     int64
    head    int
    tail    int
    count   int
}

type tsEntry struct {
    ts  time.Time
    val int64
}

func (r *RollingSum) Add(ts time.Time, val int64) {
    r.evict(ts)
    // append to circular buffer
    r.sum += val
}

func (r *RollingSum) Sum() int64 { return r.sum }

// RollingExtreme maintains a rolling max or min using a monotonic deque.
type RollingExtreme struct {
    Window time.Duration
    isMax  bool
    deque  []tsFloat
}
```

### 1.4 Tick Classification (Tick Rule)

For classifying volume as buy or sell:

```go
// ClassifyTick determines if a trade was buyer or seller initiated.
// Uses the tick rule: if price > last price → buy, if < → sell.
// If price == last price, use last known direction.
// ts is passed explicitly to avoid stale timestamp bugs.
func (s *StockState) ClassifyTick(price float64, volumeDelta int64, ts time.Time) {
    if price > s.LastLTP {
        s.LastDirection = 1
    } else if price < s.LastLTP {
        s.LastDirection = -1
    }
    // else: keep LastDirection (continuation)

    if s.LastDirection >= 0 {
        s.CumulativeBuyVol += volumeDelta
        s.BuyVol5m.Add(ts, volumeDelta)
    } else {
        s.CumulativeSellVol += volumeDelta
        s.SellVol5m.Add(ts, volumeDelta)
    }
    s.LastLTP = price
}
```

### 1.5 State Update Flow

Called on every tick event from the feed:

```go
// UpdateOnTick is called by the feed layer for every tick event.
func (s *StockState) UpdateOnTick(ltp float64, volume int64, ts time.Time) {
    volumeDelta := volume - s.CumulativeVolume
    if volumeDelta <= 0 {
        // Price-only update (no new volume)
        s.LTP = ltp
        s.LastTickTS = ts
        if ltp > s.DayHigh { s.DayHigh = ltp }
        if ltp < s.DayLow || s.DayLow == 0 { s.DayLow = ltp }
        return
    }

    // Update price
    s.LTP = ltp
    s.LastTickTS = ts
    if s.DayOpen == 0 { s.DayOpen = ltp }
    if ltp > s.DayHigh { s.DayHigh = ltp }
    if ltp < s.DayLow || s.DayLow == 0 { s.DayLow = ltp }

    // Update volume primitives
    s.CumulativeVolume = volume
    s.CumulativeTurnover += ltp * float64(volumeDelta)
    s.UpdateCount++

    // Classify buy/sell (ts passed explicitly — Codex fix #2)
    s.ClassifyTick(ltp, volumeDelta, ts)

    // Rolling windows
    s.Volume1m.Add(ts, volumeDelta)
    s.Volume5m.Add(ts, volumeDelta)
    s.Updates1m.Add(ts, 1)
    s.High5m.Add(ts, ltp)
    s.Low5m.Add(ts, ltp)
}

// UpdateOnDepth is called by the feed layer for every depth event.
func (s *StockState) UpdateOnDepth(bids, asks []DepthLevel) {
    s.TotalBidQty = 0
    s.TotalAskQty = 0
    for i := 0; i < 5 && i < len(bids); i++ {
        s.BidPrices[i] = bids[i].Price
        s.BidQtys[i] = int64(bids[i].Qty)
        s.TotalBidQty += s.BidQtys[i]
    }
    for i := 0; i < 5 && i < len(asks); i++ {
        s.AskPrices[i] = asks[i].Price
        s.AskQtys[i] = int64(asks[i].Qty)
        s.TotalAskQty += s.AskQtys[i]
    }
}
```

---

## Phase 2: Feature Layer

### 2.1 Feature Function Contract

Every feature is a pure function that reads from state and returns a value:

```go
// FeatureFunc computes a single feature value from stock state.
// Must be pure (no side effects) and fast (< 1µs).
type FeatureFunc func(stock *StockState, market *MarketState) float64

// FeatureDef defines a registered feature.
type FeatureDef struct {
    Name         string      // e.g. "vwap_dist_bps"
    Version      int         // Increment when computation logic changes
    Category     string      // e.g. "volume", "price", "book", "breadth"
    WarmupTicks  int         // Minimum ticks before feature is reliable
    Dependencies []string    // Names of features this depends on (for ordering)
    DefaultValue float64     // Value to return when not warmed up (NaN or 0)
    Compute      FeatureFunc
}
```

### 2.2 Feature Registry

```go
// Registry holds all registered feature functions.
// Features are stored in ordered slice for cache-friendly iteration.
// ComputeAll returns a reusable float64 slice (not a map) to minimize GC pressure.
type Registry struct {
    features []FeatureDef
    nameIdx  map[string]int
    version  int // global feature set version, incremented on any registration change
}

func NewRegistry() *Registry {
    r := &Registry{nameIdx: make(map[string]int), version: 1}
    // Register all core features
    r.Register(FeatureDef{Name: "vwap", Version: 1, Category: "price", WarmupTicks: 10, Compute: featureVWAP})
    r.Register(FeatureDef{Name: "vwap_dist_bps", Version: 1, Category: "price", WarmupTicks: 10, Compute: featureVWAPDistBps})
    r.Register(FeatureDef{Name: "volume_spike_z", Version: 1, Category: "volume", WarmupTicks: 50, Compute: featureVolumeSpikeZ})
    r.Register(FeatureDef{Name: "buy_pressure", Version: 1, Category: "volume", WarmupTicks: 20, Compute: featureBuyPressure})
    r.Register(FeatureDef{Name: "buy_pressure_5m", Version: 1, Category: "volume", WarmupTicks: 20, Compute: featureBuyPressure5m})
    r.Register(FeatureDef{Name: "book_imbalance", Version: 1, Category: "book", WarmupTicks: 1, Compute: featureBookImbalance})
    r.Register(FeatureDef{Name: "book_imbalance_weighted", Version: 1, Category: "book", WarmupTicks: 1, Compute: featureBookImbalanceWeighted})
    r.Register(FeatureDef{Name: "exhaustion", Version: 1, Category: "price", WarmupTicks: 10, Compute: featureExhaustion})
    r.Register(FeatureDef{Name: "spread_bps", Version: 1, Category: "book", WarmupTicks: 1, Compute: featureSpreadBps})
    r.Register(FeatureDef{Name: "update_intensity", Version: 1, Category: "volume", WarmupTicks: 30, Compute: featureUpdateIntensity})
    r.Register(FeatureDef{Name: "day_range_pct", Version: 1, Category: "price", WarmupTicks: 10, Compute: featureDayRangePct})
    r.Register(FeatureDef{Name: "change_pct", Version: 1, Category: "price", WarmupTicks: 1, Compute: featureChangePct})
    // Market-wide features
    r.Register(FeatureDef{Name: "breadth_ratio", Version: 1, Category: "breadth", WarmupTicks: 1, Compute: featureBreadthRatio})
    r.Register(FeatureDef{Name: "vwap_breadth", Version: 1, Category: "breadth", WarmupTicks: 50, Compute: featureVWAPBreadth})
    r.Register(FeatureDef{Name: "market_buy_pressure", Version: 1, Category: "breadth", WarmupTicks: 20, Compute: featureMarketBuyPressure})
    return r
}

func (r *Registry) Register(def FeatureDef) {
    r.nameIdx[def.Name] = len(r.features)
    r.features = append(r.features, def)
}

// FeatureVector is a fixed-size ordered slice matching the registry order.
// Reused via sync.Pool to minimize allocations.
type FeatureVector struct {
    Values  []float64 // ordered by registry index
    Ready   []bool    // true if feature has passed warmup
    Version int       // feature set version for backtesting provenance
}

// ComputeAll runs all features for a stock and returns a FeatureVector.
// Uses pooled vectors to avoid GC pressure at tick rate.
func (r *Registry) ComputeAll(stock *StockState, market *MarketState) *FeatureVector {
    fv := r.pool.Get().(*FeatureVector)
    fv.Version = r.version
    for i, f := range r.features {
        if stock.UpdateCount < int64(f.WarmupTicks) {
            fv.Values[i] = f.DefaultValue
            fv.Ready[i] = false
        } else {
            fv.Values[i] = f.Compute(stock, market)
            fv.Ready[i] = true
        }
    }
    return fv
}

// ToMap converts to map for JSON serialization (hub broadcast).
// Only includes ready features.
func (r *Registry) ToMap(fv *FeatureVector) map[string]float64 {
    m := make(map[string]float64, len(r.features))
    for i, f := range r.features {
        if fv.Ready[i] {
            m[f.Name] = fv.Values[i]
        }
    }
    return m
}
```

### 2.3 Core Feature Implementations

```go
// --- Price Features ---

func featureVWAP(s *StockState, m *MarketState) float64 {
    if s.CumulativeVolume == 0 { return 0 }
    return s.CumulativeTurnover / float64(s.CumulativeVolume)
}

func featureVWAPDistBps(s *StockState, m *MarketState) float64 {
    vwap := featureVWAP(s, m)
    if vwap == 0 { return 0 }
    return (s.LTP - vwap) / vwap * 10000
}

func featureChangePct(s *StockState, m *MarketState) float64 {
    if s.PrevClose == 0 { return 0 }
    return (s.LTP - s.PrevClose) / s.PrevClose * 100
}

func featureDayRangePct(s *StockState, m *MarketState) float64 {
    if s.PrevClose == 0 { return 0 }
    return (s.DayHigh - s.DayLow) / s.PrevClose * 100
}

func featureExhaustion(s *StockState, m *MarketState) float64 {
    // How much of the typical daily range has been used
    if s.ATR14d == 0 { return 0 }
    return (s.DayHigh - s.DayLow) / s.ATR14d
}

// --- Volume Features ---

func featureVolumeSpikeZ(s *StockState, m *MarketState) float64 {
    // Z-score of current 5-min volume vs same-slot historical baseline.
    // Uses REAL preloaded mean + stddev per slot (not approximated).
    currentVol := float64(s.Volume5m.Sum())
    slot := timeToSlot(s.LastTickTS)
    baseline, ok := s.VolumeSlot[slot]
    if !ok || baseline.Mean == 0 || baseline.StdDev == 0 || baseline.Samples < 5 {
        return 0 // insufficient baseline data
    }
    return (currentVol - baseline.Mean) / baseline.StdDev
}

func featureBuyPressure(s *StockState, m *MarketState) float64 {
    // Day-level buy pressure ratio
    total := s.CumulativeBuyVol + s.CumulativeSellVol
    if total == 0 { return 0.5 }
    return float64(s.CumulativeBuyVol) / float64(total)
}

func featureBuyPressure5m(s *StockState, m *MarketState) float64 {
    // 5-minute rolling buy pressure
    buy := s.BuyVol5m.Sum()
    sell := s.SellVol5m.Sum()
    total := buy + sell
    if total == 0 { return 0.5 }
    return float64(buy) / float64(total)
}

func featureUpdateIntensity(s *StockState, m *MarketState) float64 {
    // Feed updates per minute (activity proxy, NOT trade count).
    // Renamed from "trade_intensity" per Codex fix #3.
    return float64(s.Updates1m.Sum())
}

// NOTE: avg_trade_size REMOVED per Codex fix #4.
// Without true execution/trade prints from the exchange, this metric is meaningless.
// Fyers sends cumulative volume updates, not individual trade events.

// --- Book Features ---

func featureBookImbalance(s *StockState, m *MarketState) float64 {
    // Simple bid/ask imbalance at best level
    total := s.BidQtys[0] + s.AskQtys[0]
    if total == 0 { return 0.5 }
    return float64(s.BidQtys[0]) / float64(total)
}

func featureBookImbalanceWeighted(s *StockState, m *MarketState) float64 {
    // Weighted 5-level imbalance (closer levels count more)
    weights := [5]float64{5, 3, 2, 1, 0.5} // best level = highest weight
    var bidWeighted, askWeighted float64
    for i := 0; i < 5; i++ {
        bidWeighted += float64(s.BidQtys[i]) * weights[i]
        askWeighted += float64(s.AskQtys[i]) * weights[i]
    }
    total := bidWeighted + askWeighted
    if total == 0 { return 0.5 }
    return bidWeighted / total
}

func featureSpreadBps(s *StockState, m *MarketState) float64 {
    if s.BidPrices[0] == 0 { return 0 }
    return (s.AskPrices[0] - s.BidPrices[0]) / s.BidPrices[0] * 10000
}

// --- Breadth Features (market-wide) ---

func featureBreadthRatio(s *StockState, m *MarketState) float64 {
    total := m.StocksUp + m.StocksDown
    if total == 0 { return 0.5 }
    return float64(m.StocksUp) / float64(total)
}

func featureVWAPBreadth(s *StockState, m *MarketState) float64 {
    if m.TotalStocks == 0 { return 0 }
    return float64(m.StocksAboveVWAP) / float64(m.TotalStocks)
}

func featureMarketBuyPressure(s *StockState, m *MarketState) float64 {
    total := m.TotalMarketBuyVol + m.TotalMarketSellVol
    if total == 0 { return 0.5 }
    return float64(m.TotalMarketBuyVol) / float64(total)
}

// --- Helpers ---

// timeToSlot converts a timestamp to a 5-minute slot index since market open (9:15).
// Slot 0 = 9:15-9:20, Slot 1 = 9:20-9:25, ..., Slot 74 = 15:25-15:30
func timeToSlot(t time.Time) int {
    minutesSinceOpen := t.Hour()*60 + t.Minute() - 555 // 555 = 9*60+15
    if minutesSinceOpen < 0 { return 0 }
    return minutesSinceOpen / 5
}
```

---

## Phase 3: Output Layer

### 3.1 Enriched Hub Broadcast

Currently the hub broadcasts raw tick data. After this change, it broadcasts tick + features:

```json
{
    "type": "tick",
    "isin": "INE002A01018",
    "symbol": "RELIANCE",
    "ts": 1679560575,
    "ltp": 2850.50,
    "volume": 2340000,
    "open": 2838.00,
    "high": 2853.00,
    "low": 2835.00,
    "prevClose": 2832.00,
    "features": {
        "vwap": 2842.30,
        "vwap_dist_bps": 28.8,
        "volume_spike_z": 3.4,
        "buy_pressure": 0.62,
        "buy_pressure_5m": 0.71,
        "book_imbalance": 0.61,
        "book_imbalance_weighted": 0.58,
        "exhaustion": 0.21,
        "spread_bps": 3.5,
        "update_intensity": 45,
        "day_range_pct": 0.63,
        "change_pct": 0.65,
        "breadth_ratio": 0.58,
        "vwap_breadth": 0.54,
        "market_buy_pressure": 0.56
    }
}
```

**Note:** `isin` comes first (it's the primary key). `symbol` is display-only.

### 3.2 Tick-Level Feature Vector Storage (atdb)

Every tick's feature vector is persisted asynchronously for backtesting and ML training:

```sql
CREATE TABLE feature_vectors (
    ts TIMESTAMPTZ NOT NULL,
    isin TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ltp DOUBLE PRECISION,
    features JSONB NOT NULL,
    feature_set_version INT NOT NULL DEFAULT 1
);
SELECT create_hypertable('feature_vectors', 'ts');
CREATE INDEX idx_feature_vectors_isin ON feature_vectors (isin, ts DESC);

-- Compression policy (features are write-once, read for backtesting)
ALTER TABLE feature_vectors SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'isin',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('feature_vectors', INTERVAL '1 day');
```

**Async write pipeline:**
```go
// FeatureWriter batches feature vectors and bulk-inserts to atdb.
// Non-blocking: ComputeAll pushes to a channel, writer goroutine drains it.
type FeatureWriter struct {
    ch   chan featureRow
    pool *pgxpool.Pool
    done chan struct{}
}

type featureRow struct {
    ts       time.Time
    isin     string
    symbol   string
    ltp      float64
    features map[string]float64
    version  int
}

const (
    featureWriterBufSize = 100_000       // channel buffer
    featureFlushInterval = 500 * time.Millisecond
    featureFlushMaxRows  = 10_000
)

func (w *FeatureWriter) Write(row featureRow) {
    select {
    case w.ch <- row:
    default:
        // Channel full — drop oldest (back-pressure)
        // Log warning periodically
    }
}

// flush goroutine: drains channel, bulk-inserts every 500ms or 10K rows
func (w *FeatureWriter) flushLoop() {
    batch := make([]featureRow, 0, featureFlushMaxRows)
    ticker := time.NewTicker(featureFlushInterval)
    for {
        select {
        case row := <-w.ch:
            batch = append(batch, row)
            if len(batch) >= featureFlushMaxRows {
                w.bulkInsert(batch)
                batch = batch[:0]
            }
        case <-ticker.C:
            if len(batch) > 0 {
                w.bulkInsert(batch)
                batch = batch[:0]
            }
        case <-w.done:
            if len(batch) > 0 {
                w.bulkInsert(batch)
            }
            return
        }
    }
}
```

### 3.3 REST Endpoint

The Go engine exposes REST on a separate port (e.g. 3003) to avoid conflating with the Hub WS on 3002:

```
GET http://127.0.0.1:3003/features              → all stocks, latest features
GET http://127.0.0.1:3003/features/:isin         → single stock features
GET http://127.0.0.1:3003/features/market        → market-wide features only
GET http://127.0.0.1:3003/features/meta          → feature registry metadata (names, versions, categories)
```

Hono API server proxies these to the dashboard.

---

## Phase 4: Baseline Pre-loading

At engine startup (before market open), load baselines from atdb.
**Uses last N trading dates, NOT calendar days** (Codex fix #6).

```go
func PreloadBaselines(pool *pgxpool.Pool, states map[string]*StockState) error {
    ctx := context.Background()

    // 0. Get last N trading dates (not calendar days)
    tradingDates := []time.Time{}
    rows, _ := pool.Query(ctx,
        `SELECT DISTINCT trade_date FROM nse_cm_bhavcopy
         ORDER BY trade_date DESC LIMIT 15`)
    for rows.Next() {
        var d time.Time
        rows.Scan(&d)
        tradingDates = append(tradingDates, d)
    }
    rows.Close()
    if len(tradingDates) == 0 {
        return fmt.Errorf("no trading dates in bhavcopy")
    }
    lastDate := tradingDates[0]
    tenthDate := tradingDates[min(9, len(tradingDates)-1)]
    fourteenthDate := tradingDates[min(13, len(tradingDates)-1)]

    // 1. Previous close from bhavcopy (most recent trading day)
    rows, _ = pool.Query(ctx,
        `SELECT isin, close_price FROM nse_cm_bhavcopy
         WHERE trade_date = $1`, lastDate)
    for rows.Next() {
        var isin string; var close float64
        rows.Scan(&isin, &close)
        if s, ok := states[isin]; ok { s.PrevClose = close }
    }
    rows.Close()

    // 2. 14-trading-day ATR
    rows, _ = pool.Query(ctx,
        `SELECT isin, AVG(high_price - low_price) as atr
         FROM nse_cm_bhavcopy
         WHERE trade_date >= $1
         GROUP BY isin`, fourteenthDate)
    for rows.Next() {
        var isin string; var atr float64
        rows.Scan(&isin, &atr)
        if s, ok := states[isin]; ok { s.ATR14d = atr }
    }
    rows.Close()

    // 3. Volume by slot — mean + stddev per 5-min slot over last 10 trading days
    rows, _ = pool.Query(ctx,
        `SELECT isin,
                (EXTRACT(HOUR FROM ts AT TIME ZONE 'Asia/Kolkata')::int * 60 +
                 EXTRACT(MINUTE FROM ts AT TIME ZONE 'Asia/Kolkata')::int - 555) / 5 as slot,
                AVG(volume_delta) as mean_vol,
                STDDEV_SAMP(volume_delta) as std_vol,
                COUNT(*) as samples
         FROM (
             SELECT isin, ts,
                    volume - LAG(volume) OVER (PARTITION BY isin, DATE(ts AT TIME ZONE 'Asia/Kolkata') ORDER BY ts) as volume_delta
             FROM nse_cm_ticks
             WHERE DATE(ts AT TIME ZONE 'Asia/Kolkata') >= $1
         ) sub
         WHERE volume_delta > 0
         GROUP BY isin, slot`, tenthDate)
    for rows.Next() {
        var isin string; var slot int; var mean, std float64; var samples int
        rows.Scan(&isin, &slot, &mean, &std, &samples)
        if s, ok := states[isin]; ok {
            if s.VolumeSlot == nil { s.VolumeSlot = make(map[int]VolumeSlotBaseline) }
            s.VolumeSlot[slot] = VolumeSlotBaseline{Mean: mean, StdDev: std, Samples: samples}
        }
    }
    rows.Close()

    // 4. 10-trading-day average daily volume
    rows, _ = pool.Query(ctx,
        `SELECT isin, AVG(total_traded_quantity) as avg_vol
         FROM nse_cm_bhavcopy
         WHERE trade_date >= $1
         GROUP BY isin`, tenthDate)
    for rows.Next() {
        var isin string; var avgVol int64
        rows.Scan(&isin, &avgVol)
        if s, ok := states[isin]; ok { s.AvgDailyVolume = avgVol }
    }
    rows.Close()

    return nil
}
```

---

## File Structure

```
engine/
├── main.go                    (existing — wire in feature engine)
├── feed/
│   ├── datasocket.go          (existing — calls featureEngine.UpdateTick)
│   ├── tbt.go                 (existing — calls featureEngine.UpdateTick)
│   ├── hub.go                 (existing — add BroadcastEnrichedTick)
│   ├── pg_writer.go           (existing)
│   └── recorder.go            (existing)
├── features/                  (NEW — all new code here)
│   ├── state.go               — StockState, MarketState, VolumeSlotBaseline
│   ├── rolling.go             — RollingSum, RollingExtreme
│   ├── registry.go            — Registry, FeatureDef, FeatureVector, ComputeAll
│   ├── price.go               — VWAP, exhaustion, change%, range%
│   ├── volume.go              — spike z-score, buy pressure, update intensity
│   ├── book.go                — imbalance, spread
│   ├── breadth.go             — market-wide breadth features
│   ├── baselines.go           — PreloadBaselines from atdb (trading-day aware)
│   ├── engine.go              — FeatureEngine (orchestrates state + registry + market delta)
│   ├── writer.go              — FeatureWriter (async bulk insert to atdb)
│   ├── rest.go                — REST /features endpoint (port 3003)
│   └── features_test.go       — Unit tests with synthetic ticks
```

---

## Integration Points

### Feed → Feature Engine (modify existing code)

In `datasocket.go` and `tbt.go`, after parsing a tick:

```go
// Existing: write raw to PG
f.pgWriter.WriteTick(row)

// NEW: update state + compute features + broadcast enriched + log to atdb
fv := f.featureEngine.OnTick(isin, symbol, ltp, volume, ts)
f.hub.BroadcastEnrichedTick(isin, symbol, row, fv)
f.featureWriter.Write(featureRow{ts, isin, symbol, ltp, fv.ToMap(), fv.Version})
```

For depth events:
```go
// Existing: write raw to PG
f.pgWriter.WriteDepth(row)

// NEW: update book state (no feature recompute — next tick will pick it up)
f.featureEngine.OnDepth(isin, bids, asks)
```

### Hub Enhancement

The hub gets `BroadcastEnrichedTick()` which includes the features map in JSON. Existing consumers that don't read `features` key are unaffected (backward compatible).

---

## Implementation Order

| Step | What | Est. Time |
|------|-------|-----------|
| 1 | `features/state.go` — StockState + MarketState + delta tracking | 1.5 hr |
| 2 | `features/rolling.go` — RollingSum + RollingExtreme | 1 hr |
| 3 | `features/engine.go` — FeatureEngine (state mgr + market delta updates) | 1.5 hr |
| 4 | `features/baselines.go` — PreloadBaselines (trading-day aware SQL) | 1 hr |
| 5 | `features/registry.go` — Registry + FeatureVector pool + warmup tracking | 1 hr |
| 6 | `features/price.go` — VWAP, exhaustion, change%, range% | 45 min |
| 7 | `features/volume.go` — spike z (real stddev), buy pressure, update intensity | 45 min |
| 8 | `features/book.go` — imbalance (simple + weighted), spread | 30 min |
| 9 | `features/breadth.go` — market-wide features | 30 min |
| 10 | `features/writer.go` — Async FeatureWriter (channel + bulk insert) | 1 hr |
| 11 | Wire into feed layer (datasocket.go, tbt.go) | 1 hr |
| 12 | Hub BroadcastEnrichedTick | 30 min |
| 13 | `features/rest.go` — REST endpoint on port 3003 | 30 min |
| 14 | atdb DDL (feature_vectors table + compression policy) | 15 min |
| 15 | Unit tests with synthetic ticks | 1.5 hr |
| 16 | Live test during market hours | 1 hr |

**Total: ~13 hours**

---

## Codex Review Fixes Incorporated

| # | Issue | Fix |
|---|-------|-----|
| 1 | MarketState incremental drift | Delta tracking via `prevWasUp/Down/AboveVWAP`, `prevBuyVol/SellVol/Volume/Turnover` per StockState |
| 2 | ClassifyTick stale timestamp | `ts` passed explicitly as parameter |
| 3 | TradeCount ≠ actual trades | Renamed to `UpdateCount`, feature renamed to `update_intensity` |
| 4 | AvgTradeSize meaningless | **Removed entirely** — needs true execution prints |
| 5 | Fake volume stddev | `VolumeSlotBaseline` struct with real `Mean` + `StdDev` + `Samples`, preloaded from atdb |
| 6 | Calendar days in baseline SQL | Uses `DISTINCT trade_date ... ORDER BY DESC LIMIT N` |

## Gemx Review Fixes Incorporated

| Issue | Fix |
|-------|-----|
| Backtesting needs feature history | `feature_vectors` hypertable with tick-level async writes (default) |
| State loss on restart | All feature vectors persisted to atdb; can replay if needed |
| Feature versioning | `feature_set_version` column + `Version` in FeatureDef |

## Ricky's Directives Incorporated

| Directive | Implementation |
|-----------|---------------|
| Tick-level feature logging (default) | Every tick → feature vector → async bulk insert to atdb |
| Universal naming (ISIN + Symbol only) | All code/DB/hub uses `isin` as key, `symbol` as display. All other names killed. |

---

## Testing Strategy

### Unit Tests
- Feed synthetic tick sequences into StockState, verify each primitive updates correctly
- Feed known tick patterns (spike, fade, reversal), verify features compute expected values
- Test RollingSum eviction at window boundaries
- Test tick classification (uptick, downtick, zero-tick continuation)
- Test MarketState delta updates (add stock, update stock, verify counts)
- Test warmup semantics (features return default before warmup threshold)

### Integration Test
- Start engine with live Fyers feed
- Connect a WS client to hub, log enriched ticks
- Verify feature values are sane:
  - VWAP is between day low and day high
  - Volume spike z ≈ 0 for normal stocks, > 2 for stocks in play
  - Book imbalance between 0 and 1
  - Exhaustion between 0 and ~2 (rarely > 1.5)
  - Breadth ratio between 0 and 1
- Verify feature_vectors table is receiving rows
- Verify REST endpoint returns current features

### Validation (manual, during market hours)
- Pick 5 stocks we know well (RELIANCE, TCS, HDFCBANK, INFY, SBIN)
- Watch feature vectors live on dashboard
- Cross-reference with TradingView / broker terminal
- Does volume_spike_z spike when we see real breakouts?
- Does book_imbalance shift before price moves?

---

## What This Plan Does NOT Cover (Phase 2, Future)

- Python screener layer (consumes features from hub)
- Regime classifier integration (governs screener sensitivity)
- Signal generation and alerting
- Dashboard feature visualization
- Candle building (deferred — can add later as a feature)
- Historical feature backfill from raw ticks
- Feature importance analysis / ML model training on features
