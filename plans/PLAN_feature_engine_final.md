# Plan: Go Feature Engine — FINAL

**Status:** APPROVED — ready for Coder
**Date:** 2026-03-23
**Owner:** Gxozt (plan) → Coder (implementation)
**Reviewed by:** Codex, Gemx, Ricky — 3 review rounds

---

## Foundational Decisions

### 1. Universal Naming

| Name | Purpose | Example |
|------|---------|---------|
| `isin` | Primary key everywhere — code, DB, maps, hub | `INE002A01018` |
| `symbol` | Display name — logs, UI, human-readable | `RELIANCE` |

Dead: ~~security_id~~, ~~scrip~~, ~~instrument~~, ~~trading_symbol~~.
Fyers naming (`NSE:RELIANCE-EQ`) mapped at feed boundary. Never leaks downstream.

### 2. No Feature Persistence

Features are NOT stored. Raw ticks + depth are already stored in atdb (`nse_cm_ticks`, `nse_cm_depth_5`). Every feature can be deterministically recomputed from raw data.

- **Live:** Features computed in-memory, broadcast via hub, consumed by screeners/dashboard
- **Backtest:** Replay raw ticks through feature engine → get historical features
- **Benefit:** If we change a feature formula, backtests automatically use the new formula. No stale data.
- **Removed:** ~~feature_vectors table~~, ~~feature_snapshots table~~, ~~FeatureWriter~~, ~~async write pipeline~~

### 3. Build Order (Codex's layered discipline)

1. Core correctness spine (state, event loop, session, feed guards)
2. Output + observability (hub broadcast, immutable snapshots, quality flags)
3. Feature families (15 core → sector features)
4. Config + tuning surface

---

## Architecture

```
Fyers Feed (DataSocket + TBT)
       ↓
  Go Engine Process (single binary)
  ┌──────────────────────────────────────────────┐
  │  Feed Layer (existing)                        │
  │    → maps Fyers symbols → ISIN + Symbol       │
  │    → feed guards (monotonic, sanity, reconnect)│
  │                                               │
  │  State Layer (NEW)                            │
  │    → StockState per ISIN                      │
  │    → SectorState per sector                   │
  │    → MarketState (delta-tracked)              │
  │    → Session lifecycle (start/end/reset)      │
  │                                               │
  │  Feature Layer (NEW)                          │
  │    → Feature registry (plugin architecture)   │
  │    → Per-feature trigger: tick|depth|hybrid    │
  │    → Per-feature readiness: Ready() bool      │
  │    → Dependency caching per compute cycle      │
  │                                               │
  │  Output Layer                                 │
  │    → Hub WS broadcast (enriched tick+features) │
  │    → Immutable snapshots for REST readers      │
  │    → REST /features endpoint (port 3003)       │
  └──────────────────────────────────────────────┘
       ↓ hub                    ↓ existing
  Python Screeners         atdb (raw ticks + depth only)
  Hono API → Dashboard
```

**What goes to atdb:** Raw ticks + depth (already happening). Nothing else from the feature engine.
**What goes to hub:** Enriched tick JSON with computed features.
**What goes to REST:** Immutable snapshots of latest state.

---

## Phase 1: Core Correctness Spine

### 1.1 Single-Writer Event Loop

All state mutations happen in ONE goroutine. No locks on hot path.

```go
// FeatureEngine is the central orchestrator.
// All mutations flow through the event loop channel.
type FeatureEngine struct {
    stocks   map[string]*StockState   // keyed by ISIN
    sectors  map[string]*SectorState  // keyed by sector name
    market   *MarketState
    registry *Registry
    config   *EngineConfig

    // Event loop
    tickCh   chan TickEvent   // from feed goroutines
    depthCh  chan DepthEvent  // from feed goroutines
    
    // Immutable snapshot (atomic pointer swap)
    latestSnapshot atomic.Pointer[EngineSnapshot]
}

type TickEvent struct {
    ISIN   string
    Symbol string
    LTP    float64
    Volume int64
    TS     time.Time
}

type DepthEvent struct {
    ISIN string
    Bids []DepthLevel
    Asks []DepthLevel
    TS   time.Time
}

// Run is the single-writer event loop. Only this goroutine mutates state.
func (e *FeatureEngine) Run(ctx context.Context) {
    sessionTicker := time.NewTicker(1 * time.Second)
    for {
        select {
        case tick := <-e.tickCh:
            e.handleTick(tick)
        case depth := <-e.depthCh:
            e.handleDepth(depth)
        case <-sessionTicker.C:
            e.handleTimer()
        case <-ctx.Done():
            return
        }
    }
}
```

### 1.2 StockState

```go
type StockState struct {
    ISIN     string
    Symbol   string
    SectorID string // e.g. "NIFTY_IT", "NIFTY_BANK", "NIFTY_FMCG"

    // === Tick-Updated Primitives ===
    LTP                float64
    DayOpen            float64
    DayHigh            float64
    DayLow             float64
    PrevClose          float64   // pre-loaded from atdb
    LastTickTS         time.Time

    CumulativeVolume   int64
    CumulativeTurnover float64
    CumulativeBuyVol   int64
    CumulativeSellVol  int64
    UpdateCount        int64     // feed updates, NOT trade count

    LastDirection      int8      // +1 uptick, -1 downtick, 0 unchanged
    LastLTP            float64

    // === Depth-Updated Primitives ===
    BidPrices   [5]float64
    BidQtys     [5]int64
    AskPrices   [5]float64
    AskQtys     [5]int64
    TotalBidQty int64
    TotalAskQty int64
    HasDepth    bool      // true after first depth event (for readiness)
    LastDepthTS time.Time

    // === Rolling Windows ===
    Volume1m  *RollingSum     // 60s window
    Volume5m  *RollingSum     // 300s window
    BuyVol5m  *RollingSum     // 300s window
    SellVol5m *RollingSum     // 300s window
    Updates1m *RollingSum     // 60s window (activity proxy)
    High5m    *RollingExtreme // 300s max
    Low5m     *RollingExtreme // 300s min

    // === Pre-Loaded Baselines ===
    ATR14d         float64
    AvgDailyVolume int64
    VolumeSlot     map[int]VolumeSlotBaseline // mean + stddev per 5-min slot

    // === Delta Tracking (for MarketState/SectorState) ===
    prevWasUp        bool
    prevWasDown      bool
    prevWasAboveVWAP bool
    prevBuyVol       int64
    prevSellVol      int64
    prevVolume       int64
    prevTurnover     float64
}

type VolumeSlotBaseline struct {
    Mean    float64
    StdDev  float64
    Samples int
}
```

### 1.3 SectorState

```go
type SectorState struct {
    Name        string   // e.g. "NIFTY_BANK"
    MemberISINs []string // populated at startup from sector mapping

    StocksUp        int
    StocksDown      int
    StocksAboveVWAP int
    TotalStocks     int
    TotalBuyVol     int64
    TotalSellVol    int64
    TotalVolume     int64
    TotalTurnover   float64
}

// UpdateFromStock does delta-update same as MarketState
func (s *SectorState) UpdateFromStock(stock *StockState, vwap float64) {
    // Same delta pattern as MarketState.UpdateFromStock
    // Remove old contribution → add new contribution
}
```

### 1.4 MarketState

```go
type MarketState struct {
    TotalStocks     int
    StocksUp        int
    StocksDown      int
    StocksFlat      int
    StocksAboveVWAP int

    TotalMarketBuyVol   int64
    TotalMarketSellVol  int64
    TotalMarketVolume   int64
    TotalMarketTurnover float64

    NiftyLTP       float64
    NiftyPrevClose float64
    NiftyDayHigh   float64
    NiftyDayLow    float64
}

// UpdateFromStock does delta-update: remove old contribution, add new.
func (m *MarketState) UpdateFromStock(s *StockState, vwap float64) {
    // Remove old
    if s.prevWasUp { m.StocksUp-- }
    if s.prevWasDown { m.StocksDown-- }
    if !s.prevWasUp && !s.prevWasDown { m.StocksFlat-- }
    if s.prevWasAboveVWAP { m.StocksAboveVWAP-- }
    m.TotalMarketBuyVol -= s.prevBuyVol
    m.TotalMarketSellVol -= s.prevSellVol
    m.TotalMarketVolume -= s.prevVolume
    m.TotalMarketTurnover -= s.prevTurnover

    // Compute new
    isUp := s.LTP > s.PrevClose && s.PrevClose > 0
    isDown := s.LTP < s.PrevClose && s.PrevClose > 0
    isAboveVWAP := vwap > 0 && s.LTP > vwap

    // Add new
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

### 1.5 Session Lifecycle

```go
type SessionState int

const (
    SessionPreOpen  SessionState = iota // before 9:00
    SessionOpen                          // 9:15 - 15:30
    SessionClosed                        // after 15:30
)

// SessionStart resets all intraday state, preserves baselines.
func (e *FeatureEngine) SessionStart(date time.Time) {
    for _, s := range e.stocks {
        s.LTP = 0
        s.DayOpen = 0
        s.DayHigh = 0
        s.DayLow = 0
        s.CumulativeVolume = 0
        s.CumulativeTurnover = 0
        s.CumulativeBuyVol = 0
        s.CumulativeSellVol = 0
        s.UpdateCount = 0
        s.LastDirection = 0
        s.HasDepth = false
        s.Volume1m.Reset()
        s.Volume5m.Reset()
        s.BuyVol5m.Reset()
        s.SellVol5m.Reset()
        s.Updates1m.Reset()
        s.High5m.Reset()
        s.Low5m.Reset()
        // Preserve: PrevClose, ATR14d, AvgDailyVolume, VolumeSlot, SectorID
        // Reset delta tracking
        s.prevWasUp = false
        s.prevWasDown = false
        s.prevWasAboveVWAP = false
        s.prevBuyVol = 0
        s.prevSellVol = 0
        s.prevVolume = 0
        s.prevTurnover = 0
    }
    // Reset market + sector state
    *e.market = MarketState{TotalStocks: len(e.stocks)}
    for _, sec := range e.sectors {
        *sec = SectorState{Name: sec.Name, MemberISINs: sec.MemberISINs, TotalStocks: len(sec.MemberISINs)}
    }
    logTS("[Session] Started for %s — %d stocks, %d sectors", date.Format("2006-01-02"), len(e.stocks), len(e.sectors))
}

// SessionEnd marks session closed. Rejects further ticks until next SessionStart.
func (e *FeatureEngine) SessionEnd() {
    e.sessionState = SessionClosed
    logTS("[Session] Ended — final market: %d up, %d down, %d flat", e.market.StocksUp, e.market.StocksDown, e.market.StocksFlat)
}
```

### 1.6 Feed Guards

```go
type FeedGuard struct {
    lastTS     time.Time
    lastVolume int64
    lastLTP    float64
    config     *GuardConfig
}

type GuardConfig struct {
    MaxPriceJumpPct  float64 // reject if |LTP change| > X% in one tick (default 20%)
    MaxSpreadBps     float64 // reject if spread > X bps (default 500)
    MinLTP           float64 // reject if LTP <= 0
    AllowVolumeReset bool    // handle reconnect volume resets gracefully
}

// ValidateTick returns true if tick is sane, false to reject.
func (g *FeedGuard) ValidateTick(isin string, ltp float64, volume int64, ts time.Time) (bool, string) {
    // Reject zero/negative LTP
    if ltp <= 0 {
        return false, "ltp <= 0"
    }

    // Reject backward timestamps
    if !g.lastTS.IsZero() && ts.Before(g.lastTS) {
        return false, "timestamp went backward"
    }

    // Reject insane price jumps (circuit breaker: NSE max is 20%)
    if g.lastLTP > 0 {
        jumpPct := math.Abs(ltp-g.lastLTP) / g.lastLTP * 100
        if jumpPct > g.config.MaxPriceJumpPct {
            return false, fmt.Sprintf("price jump %.1f%% exceeds max %.1f%%", jumpPct, g.config.MaxPriceJumpPct)
        }
    }

    // Handle volume reset (reconnect scenario)
    if volume < g.lastVolume && g.config.AllowVolumeReset {
        // Volume went backward — likely feed reconnect
        // Accept but flag for special handling
        logTS("[FeedGuard] %s volume reset: %d → %d (reconnect?)", isin, g.lastVolume, volume)
    }

    g.lastTS = ts
    g.lastVolume = volume
    g.lastLTP = ltp
    return true, ""
}
```

### 1.7 RollingSum / RollingExtreme

```go
// RollingSum maintains a time-windowed sum using circular buffer.
type RollingSum struct {
    window  time.Duration
    buf     []tsEntry
    cap     int
    head    int
    count   int
    sum     int64
}

type tsEntry struct {
    ts  time.Time
    val int64
}

func NewRollingSum(window time.Duration, capacity int) *RollingSum {
    return &RollingSum{
        window: window,
        buf:    make([]tsEntry, capacity),
        cap:    capacity,
    }
}

func (r *RollingSum) Add(ts time.Time, val int64) {
    r.evict(ts)
    idx := (r.head + r.count) % r.cap
    r.buf[idx] = tsEntry{ts, val}
    r.count++
    r.sum += val
}

func (r *RollingSum) evict(now time.Time) {
    cutoff := now.Add(-r.window)
    for r.count > 0 && r.buf[r.head].ts.Before(cutoff) {
        r.sum -= r.buf[r.head].val
        r.head = (r.head + 1) % r.cap
        r.count--
    }
}

func (r *RollingSum) Sum() int64 { return r.sum }
func (r *RollingSum) Count() int { return r.count }

func (r *RollingSum) Reset() {
    r.head = 0
    r.count = 0
    r.sum = 0
}

// RollingExtreme tracks rolling max or min using monotonic deque.
type RollingExtreme struct {
    window time.Duration
    isMax  bool
    deque  []tsFloat
}

type tsFloat struct {
    ts  time.Time
    val float64
}

func (r *RollingExtreme) Add(ts time.Time, val float64) {
    // Evict expired
    cutoff := ts.Add(-r.window)
    for len(r.deque) > 0 && r.deque[0].ts.Before(cutoff) {
        r.deque = r.deque[1:]
    }
    // Maintain monotonicity
    if r.isMax {
        for len(r.deque) > 0 && r.deque[len(r.deque)-1].val <= val {
            r.deque = r.deque[:len(r.deque)-1]
        }
    } else {
        for len(r.deque) > 0 && r.deque[len(r.deque)-1].val >= val {
            r.deque = r.deque[:len(r.deque)-1]
        }
    }
    r.deque = append(r.deque, tsFloat{ts, val})
}

func (r *RollingExtreme) Value() float64 {
    if len(r.deque) == 0 { return 0 }
    return r.deque[0].val
}

func (r *RollingExtreme) Reset() { r.deque = r.deque[:0] }
```

### 1.8 Tick Classification

```go
// ClassifyTick uses tick rule. ts passed explicitly (Codex fix #2).
func (s *StockState) ClassifyTick(price float64, volumeDelta int64, ts time.Time) {
    if price > s.LastLTP {
        s.LastDirection = 1
    } else if price < s.LastLTP {
        s.LastDirection = -1
    }

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

### 1.9 State Update Flow

```go
func (e *FeatureEngine) handleTick(ev TickEvent) {
    s := e.stocks[ev.ISIN]
    if s == nil { return }

    // Feed guard
    guard := e.guards[ev.ISIN]
    if ok, reason := guard.ValidateTick(ev.ISIN, ev.LTP, ev.Volume, ev.TS); !ok {
        logTS("[FeedGuard] REJECT %s: %s", ev.ISIN, reason)
        return
    }

    // Session gate
    if e.sessionState != SessionOpen { return }

    // Volume delta
    volumeDelta := ev.Volume - s.CumulativeVolume
    if volumeDelta <= 0 {
        // Price-only update
        s.LTP = ev.LTP
        s.LastTickTS = ev.TS
        if ev.LTP > s.DayHigh { s.DayHigh = ev.LTP }
        if ev.LTP < s.DayLow || s.DayLow == 0 { s.DayLow = ev.LTP }
    } else {
        s.LTP = ev.LTP
        s.LastTickTS = ev.TS
        if s.DayOpen == 0 { s.DayOpen = ev.LTP }
        if ev.LTP > s.DayHigh { s.DayHigh = ev.LTP }
        if ev.LTP < s.DayLow || s.DayLow == 0 { s.DayLow = ev.LTP }

        s.CumulativeVolume = ev.Volume
        s.CumulativeTurnover += ev.LTP * float64(volumeDelta)
        s.UpdateCount++

        s.ClassifyTick(ev.LTP, volumeDelta, ev.TS)

        s.Volume1m.Add(ev.TS, volumeDelta)
        s.Volume5m.Add(ev.TS, volumeDelta)
        s.Updates1m.Add(ev.TS, 1)
        s.High5m.Add(ev.TS, ev.LTP)
        s.Low5m.Add(ev.TS, ev.LTP)
    }

    // Compute VWAP (cached for this cycle)
    vwap := 0.0
    if s.CumulativeVolume > 0 {
        vwap = s.CumulativeTurnover / float64(s.CumulativeVolume)
    }

    // Delta-update market + sector
    e.market.UpdateFromStock(s, vwap)
    if sec, ok := e.sectors[s.SectorID]; ok {
        sec.UpdateFromStock(s, vwap)
    }

    // Compute tick-triggered features
    fv := e.registry.ComputeTriggered(s, e.market, e.sectors[s.SectorID], TriggerTick)

    // Broadcast enriched tick
    e.hub.BroadcastEnrichedTick(s, fv)

    // Update immutable snapshot
    e.updateSnapshot(s, fv)
}

func (e *FeatureEngine) handleDepth(ev DepthEvent) {
    s := e.stocks[ev.ISIN]
    if s == nil { return }
    if e.sessionState != SessionOpen { return }

    s.TotalBidQty = 0
    s.TotalAskQty = 0
    for i := 0; i < 5 && i < len(ev.Bids); i++ {
        s.BidPrices[i] = ev.Bids[i].Price
        s.BidQtys[i] = int64(ev.Bids[i].Qty)
        s.TotalBidQty += s.BidQtys[i]
    }
    for i := 0; i < 5 && i < len(ev.Asks); i++ {
        s.AskPrices[i] = ev.Asks[i].Price
        s.AskQtys[i] = int64(ev.Asks[i].Qty)
        s.TotalAskQty += s.AskQtys[i]
    }
    s.HasDepth = true
    s.LastDepthTS = ev.TS

    // Compute depth-triggered features only
    fv := e.registry.ComputeTriggered(s, e.market, e.sectors[s.SectorID], TriggerDepth)

    // Broadcast depth feature update
    e.hub.BroadcastDepthFeatures(s, fv)

    e.updateSnapshot(s, fv)
}

func (e *FeatureEngine) handleTimer() {
    // Compute timer-triggered features (e.g. heavy aggregates)
    // Run every 1s from the session ticker
}
```

---

## Phase 2: Feature Layer

### 2.1 Feature Registry with Trigger Policies

```go
type TriggerType int

const (
    TriggerTick   TriggerType = 1 << iota // compute on tick events
    TriggerDepth                           // compute on depth events
    TriggerTimer                           // compute on 1s timer
    TriggerHybrid = TriggerTick | TriggerDepth
)

type FeatureDef struct {
    Name     string
    Version  int
    Category string      // "price", "volume", "book", "breadth", "sector"
    Trigger  TriggerType
    Ready    func(s *StockState, m *MarketState) bool
    Compute  func(s *StockState, m *MarketState, sec *SectorState) float64
}

type Registry struct {
    features []FeatureDef
    nameIdx  map[string]int
    version  int

    // Grouped by trigger for efficient dispatch
    tickFeatures  []int // indices of tick-triggered features
    depthFeatures []int // indices of depth-triggered features
    timerFeatures []int // indices of timer-triggered features
}

// ComputeTriggered runs only features matching the trigger type.
// Returns ordered float64 slice (pooled to minimize GC).
func (r *Registry) ComputeTriggered(s *StockState, m *MarketState, sec *SectorState, trigger TriggerType) *FeatureVector {
    fv := r.pool.Get().(*FeatureVector)
    fv.Version = r.version

    var indices []int
    switch trigger {
    case TriggerTick:
        indices = r.tickFeatures
    case TriggerDepth:
        indices = r.depthFeatures
    case TriggerTimer:
        indices = r.timerFeatures
    }

    for _, i := range indices {
        f := r.features[i]
        if f.Ready != nil && !f.Ready(s, m) {
            fv.Values[i] = 0
            fv.Ready[i] = false
        } else {
            fv.Values[i] = f.Compute(s, m, sec)
            fv.Ready[i] = true
        }
    }
    return fv
}
```

### 2.2 Feature Registration

```go
func NewRegistry() *Registry {
    r := &Registry{nameIdx: make(map[string]int), version: 1}

    // --- Price Features (tick-triggered) ---
    r.Register(FeatureDef{
        Name: "vwap", Version: 1, Category: "price",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool { return s.CumulativeVolume > 0 },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            return s.CumulativeTurnover / float64(s.CumulativeVolume)
        },
    })
    r.Register(FeatureDef{
        Name: "vwap_dist_bps", Version: 1, Category: "price",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool { return s.CumulativeVolume > 0 },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            vwap := s.CumulativeTurnover / float64(s.CumulativeVolume)
            return (s.LTP - vwap) / vwap * 10000
        },
    })
    r.Register(FeatureDef{
        Name: "change_pct", Version: 1, Category: "price",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool { return s.PrevClose > 0 },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            return (s.LTP - s.PrevClose) / s.PrevClose * 100
        },
    })
    r.Register(FeatureDef{
        Name: "day_range_pct", Version: 1, Category: "price",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool { return s.PrevClose > 0 && s.DayHigh > 0 },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            return (s.DayHigh - s.DayLow) / s.PrevClose * 100
        },
    })
    r.Register(FeatureDef{
        Name: "exhaustion", Version: 1, Category: "price",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool { return s.ATR14d > 0 && s.DayHigh > 0 },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            return (s.DayHigh - s.DayLow) / s.ATR14d
        },
    })

    // --- Volume Features (tick-triggered) ---
    r.Register(FeatureDef{
        Name: "volume_spike_z", Version: 1, Category: "volume",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool {
            slot := timeToSlot(s.LastTickTS)
            b, ok := s.VolumeSlot[slot]
            return ok && b.StdDev > 0 && b.Samples >= 5
        },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            slot := timeToSlot(s.LastTickTS)
            b := s.VolumeSlot[slot]
            return (float64(s.Volume5m.Sum()) - b.Mean) / b.StdDev
        },
    })
    r.Register(FeatureDef{
        Name: "buy_pressure", Version: 1, Category: "volume",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool {
            return s.CumulativeBuyVol+s.CumulativeSellVol > 0
        },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            total := s.CumulativeBuyVol + s.CumulativeSellVol
            return float64(s.CumulativeBuyVol) / float64(total)
        },
    })
    r.Register(FeatureDef{
        Name: "buy_pressure_5m", Version: 1, Category: "volume",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool {
            return s.BuyVol5m.Sum()+s.SellVol5m.Sum() > 0
        },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            buy := s.BuyVol5m.Sum()
            total := buy + s.SellVol5m.Sum()
            return float64(buy) / float64(total)
        },
    })
    r.Register(FeatureDef{
        Name: "update_intensity", Version: 1, Category: "volume",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool { return true },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            return float64(s.Updates1m.Sum())
        },
    })

    // --- Book Features (depth-triggered) ---
    r.Register(FeatureDef{
        Name: "book_imbalance", Version: 1, Category: "book",
        Trigger: TriggerDepth,
        Ready: func(s *StockState, m *MarketState) bool { return s.HasDepth },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            total := s.BidQtys[0] + s.AskQtys[0]
            if total == 0 { return 0.5 }
            return float64(s.BidQtys[0]) / float64(total)
        },
    })
    r.Register(FeatureDef{
        Name: "book_imbalance_weighted", Version: 1, Category: "book",
        Trigger: TriggerDepth,
        Ready: func(s *StockState, m *MarketState) bool { return s.HasDepth },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            weights := [5]float64{5, 3, 2, 1, 0.5}
            var bid, ask float64
            for i := 0; i < 5; i++ {
                bid += float64(s.BidQtys[i]) * weights[i]
                ask += float64(s.AskQtys[i]) * weights[i]
            }
            total := bid + ask
            if total == 0 { return 0.5 }
            return bid / total
        },
    })
    r.Register(FeatureDef{
        Name: "spread_bps", Version: 1, Category: "book",
        Trigger: TriggerDepth,
        Ready: func(s *StockState, m *MarketState) bool { return s.HasDepth && s.BidPrices[0] > 0 },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            return (s.AskPrices[0] - s.BidPrices[0]) / s.BidPrices[0] * 10000
        },
    })

    // --- Breadth Features (hybrid — update on any stock tick) ---
    r.Register(FeatureDef{
        Name: "breadth_ratio", Version: 1, Category: "breadth",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool { return m.StocksUp+m.StocksDown > 0 },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            total := m.StocksUp + m.StocksDown
            return float64(m.StocksUp) / float64(total)
        },
    })
    r.Register(FeatureDef{
        Name: "vwap_breadth", Version: 1, Category: "breadth",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool { return m.TotalStocks > 0 },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            return float64(m.StocksAboveVWAP) / float64(m.TotalStocks)
        },
    })
    r.Register(FeatureDef{
        Name: "market_buy_pressure", Version: 1, Category: "breadth",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool {
            return m.TotalMarketBuyVol+m.TotalMarketSellVol > 0
        },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            total := m.TotalMarketBuyVol + m.TotalMarketSellVol
            return float64(m.TotalMarketBuyVol) / float64(total)
        },
    })

    // --- Sector Features (tick-triggered, need sector context) ---
    r.Register(FeatureDef{
        Name: "sector_breadth", Version: 1, Category: "sector",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool { return s.SectorID != "" },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            if sec == nil || sec.StocksUp+sec.StocksDown == 0 { return 0.5 }
            total := sec.StocksUp + sec.StocksDown
            return float64(sec.StocksUp) / float64(total)
        },
    })
    r.Register(FeatureDef{
        Name: "sector_buy_pressure", Version: 1, Category: "sector",
        Trigger: TriggerTick,
        Ready: func(s *StockState, m *MarketState) bool { return s.SectorID != "" },
        Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
            if sec == nil { return 0.5 }
            total := sec.TotalBuyVol + sec.TotalSellVol
            if total == 0 { return 0.5 }
            return float64(sec.TotalBuyVol) / float64(total)
        },
    })

    r.buildTriggerIndex()
    return r
}
```

### 2.3 Quality Flags

```go
type QualityFlags struct {
    Partial         bool  // some features not ready (warmup)
    BaselineMissing bool  // ATR/volume baselines not loaded
    DepthStaleMs    int64 // ms since last depth event
    TickStaleMs     int64 // ms since last tick
}

func (e *FeatureEngine) computeQuality(s *StockState) QualityFlags {
    now := time.Now()
    return QualityFlags{
        Partial:         s.UpdateCount < 20,
        BaselineMissing: s.ATR14d == 0 || len(s.VolumeSlot) == 0,
        DepthStaleMs:    now.Sub(s.LastDepthTS).Milliseconds(),
        TickStaleMs:     now.Sub(s.LastTickTS).Milliseconds(),
    }
}
```

---

## Phase 3: Output Layer

### 3.1 Immutable Snapshots

REST and hub consumers never read live mutable state. The event loop atomically swaps a snapshot pointer after each update.

```go
type EngineSnapshot struct {
    Stocks  map[string]StockSnapshot  // keyed by ISIN
    Market  MarketSnapshot
    Sectors map[string]SectorSnapshot
    TS      time.Time
}

type StockSnapshot struct {
    ISIN     string
    Symbol   string
    LTP      float64
    Features map[string]float64
    Quality  QualityFlags
}

// Called from event loop after every state mutation
func (e *FeatureEngine) updateSnapshot(s *StockState, fv *FeatureVector) {
    // Build stock snapshot with feature map
    snap := e.latestSnapshot.Load()
    // Copy-on-write: clone, update one stock, swap pointer
    newSnap := snap.Clone()
    newSnap.Stocks[s.ISIN] = StockSnapshot{
        ISIN:     s.ISIN,
        Symbol:   s.Symbol,
        LTP:      s.LTP,
        Features: e.registry.ToMap(fv),
        Quality:  e.computeQuality(s),
    }
    newSnap.TS = time.Now()
    e.latestSnapshot.Store(newSnap)
}
```

### 3.2 Hub Enriched Broadcast

```json
{
    "type": "tick",
    "isin": "INE002A01018",
    "symbol": "RELIANCE",
    "ts": 1679560575,
    "ltp": 2850.50,
    "volume": 2340000,
    "features": {
        "vwap": 2842.30,
        "vwap_dist_bps": 28.8,
        "volume_spike_z": 3.4,
        "buy_pressure": 0.62,
        "buy_pressure_5m": 0.71,
        "exhaustion": 0.21,
        "change_pct": 0.65,
        "day_range_pct": 0.63,
        "update_intensity": 45,
        "breadth_ratio": 0.58,
        "vwap_breadth": 0.54,
        "market_buy_pressure": 0.56,
        "sector_breadth": 0.62,
        "sector_buy_pressure": 0.59
    },
    "quality": {
        "partial": false,
        "baseline_missing": false,
        "depth_stale_ms": 120,
        "tick_stale_ms": 0
    }
}
```

Depth-triggered features get a separate broadcast:
```json
{
    "type": "depth_features",
    "isin": "INE002A01018",
    "symbol": "RELIANCE",
    "ts": 1679560576,
    "features": {
        "book_imbalance": 0.61,
        "book_imbalance_weighted": 0.58,
        "spread_bps": 3.5
    }
}
```

### 3.3 REST Endpoint (port 3003)

Serves immutable snapshots. Never touches live state.

```
GET /features              → all stocks latest
GET /features/:isin        → single stock
GET /features/market       → market-wide state
GET /features/sector/:name → sector state
GET /features/meta         → registry metadata (names, versions, categories, triggers)
```

---

## Phase 4: Baseline Pre-loading

Uses last N **trading dates** (not calendar days).

```go
func PreloadBaselines(pool *pgxpool.Pool, stocks map[string]*StockState, sectors map[string]*SectorState) error {
    ctx := context.Background()

    // Get last N trading dates
    tradingDates := queryTradingDates(pool, 15)
    lastDate := tradingDates[0]
    tenthDate := tradingDates[min(9, len(tradingDates)-1)]
    fourteenthDate := tradingDates[min(13, len(tradingDates)-1)]

    // 1. Previous close
    loadPrevClose(pool, stocks, lastDate)

    // 2. 14-trading-day ATR
    loadATR(pool, stocks, fourteenthDate)

    // 3. Volume slot baselines (mean + stddev + samples)
    loadVolumeSlotBaselines(pool, stocks, tenthDate)

    // 4. 10-trading-day avg daily volume
    loadAvgDailyVolume(pool, stocks, tenthDate)

    // 5. Sector membership mapping
    loadSectorMapping(pool, stocks, sectors)

    return nil
}

// loadSectorMapping queries nse_indices_daily to determine sector membership.
// Maps ISINs to sectors based on Nifty sectoral index constituents.
func loadSectorMapping(pool *pgxpool.Pool, stocks map[string]*StockState, sectors map[string]*SectorState) {
    // Query index constituents or use a static mapping table
    // Sets s.SectorID and populates SectorState.MemberISINs
}
```

---

## Phase 5: Configuration

```yaml
# features.yaml

engine:
  tick_channel_buffer: 100000
  depth_channel_buffer: 50000
  snapshot_clone_strategy: "copy_on_write"  # or "periodic" for lower overhead

windows:
  volume_1m: 60s
  volume_5m: 300s
  buy_vol_5m: 300s
  sell_vol_5m: 300s
  updates_1m: 60s
  high_5m: 300s
  low_5m: 300s

rolling_buffer_capacity:
  per_stock_1m: 1000   # max entries in 60s buffer
  per_stock_5m: 5000   # max entries in 300s buffer

feed_guards:
  max_price_jump_pct: 20.0    # NSE circuit limit
  max_spread_bps: 500.0
  min_ltp: 0.01
  allow_volume_reset: true

baselines:
  atr_trading_days: 14
  volume_slot_trading_days: 10
  avg_volume_trading_days: 10
  min_slot_samples: 5        # require ≥5 days of data per slot

session:
  pre_open_start: "09:00"
  market_open: "09:15"
  market_close: "15:30"
  reject_outside_session: true

book_weights: [5, 3, 2, 1, 0.5]   # weighted imbalance level weights

rest:
  port: 3003
  read_timeout: 5s

hub:
  port: 3002                       # existing
  broadcast_features: true         # enable enriched broadcast
```

---

## File Structure

```
engine/
├── main.go                         (existing — wire in feature engine)
├── feed/
│   ├── datasocket.go               (existing — pushes to tickCh/depthCh)
│   ├── tbt.go                      (existing — pushes to tickCh/depthCh)
│   ├── hub.go                      (existing — add BroadcastEnrichedTick)
│   ├── pg_writer.go                (existing — raw tick/depth only)
│   └── recorder.go                 (existing)
├── features/
│   ├── engine.go                   — FeatureEngine, event loop, handleTick/Depth/Timer
│   ├── state.go                    — StockState, SectorState, MarketState, delta tracking
│   ├── rolling.go                  — RollingSum, RollingExtreme
│   ├── registry.go                 — Registry, FeatureDef, FeatureVector, trigger dispatch
│   ├── features_price.go           — vwap, vwap_dist_bps, change_pct, day_range_pct, exhaustion
│   ├── features_volume.go          — volume_spike_z, buy_pressure, buy_pressure_5m, update_intensity
│   ├── features_book.go            — book_imbalance, book_imbalance_weighted, spread_bps
│   ├── features_breadth.go         — breadth_ratio, vwap_breadth, market_buy_pressure
│   ├── features_sector.go          — sector_breadth, sector_buy_pressure
│   ├── baselines.go                — PreloadBaselines (trading-day aware)
│   ├── guard.go                    — FeedGuard, ValidateTick
│   ├── session.go                  — Session lifecycle (start/end/reset)
│   ├── snapshot.go                 — EngineSnapshot, immutable copy-on-write
│   ├── quality.go                  — QualityFlags
│   ├── rest.go                     — REST /features endpoint
│   ├── config.go                   — EngineConfig, load from features.yaml
│   └── features_test.go            — Unit tests
├── features.yaml                   — Configuration file
```

---

## Implementation Order (Codex's layered discipline)

| Layer | Step | What | Est. |
|-------|------|------|------|
| **1. Correctness** | 1 | `state.go` — StockState + SectorState + MarketState + delta tracking | 2 hr |
| | 2 | `rolling.go` — RollingSum + RollingExtreme | 1 hr |
| | 3 | `guard.go` — FeedGuard + ValidateTick | 45 min |
| | 4 | `session.go` — Session lifecycle | 45 min |
| | 5 | `engine.go` — FeatureEngine + single-writer event loop | 2 hr |
| | 6 | `config.go` — Load features.yaml | 30 min |
| **2. Output** | 7 | `snapshot.go` — Immutable EngineSnapshot | 1 hr |
| | 8 | `quality.go` — QualityFlags | 30 min |
| | 9 | Hub BroadcastEnrichedTick + BroadcastDepthFeatures | 1 hr |
| | 10 | `rest.go` — REST endpoint on port 3003 | 45 min |
| **3. Features** | 11 | `registry.go` — Registry + trigger dispatch + pooling | 1 hr |
| | 12 | `features_price.go` — 5 price features | 45 min |
| | 13 | `features_volume.go` — 4 volume features | 45 min |
| | 14 | `features_book.go` — 3 book features | 30 min |
| | 15 | `features_breadth.go` — 3 breadth features | 30 min |
| | 16 | `features_sector.go` — 2 sector features | 30 min |
| | 17 | `baselines.go` — PreloadBaselines (trading-day SQL) | 1 hr |
| **4. Integration** | 18 | Wire feed layer → engine (datasocket.go, tbt.go) | 1 hr |
| | 19 | Wire main.go startup (config → baselines → engine → hub) | 1 hr |
| | 20 | Unit tests | 2 hr |
| | 21 | Live test during market hours | 1 hr |

**Total: ~20 hours**

---

## Summary of All Reviews Incorporated

| Source | Issue | Resolution |
|--------|-------|------------|
| Codex R1 | MarketState drift | Delta tracking with prev* fields |
| Codex R1 | ClassifyTick stale ts | ts passed explicitly |
| Codex R1 | TradeCount fake | Renamed UpdateCount |
| Codex R1 | AvgTradeSize meaningless | Removed |
| Codex R1 | Fake volume stddev | Real mean + stddev + samples from atdb |
| Codex R1 | Calendar days in SQL | Uses last N trading dates |
| Gemx R1 | Backtesting needs history | Raw ticks + depth = source of truth, recompute on demand |
| Gemx R1 | Feature versioning | Version field in FeatureDef + registry |
| Ricky | Universal naming | ISIN = key, Symbol = display, everything else dead |
| Ricky | No feature persistence | Killed — raw data is enough, features recomputed |
| Codex R2 | Depth-triggered recompute | Per-feature TriggerType (tick/depth/timer/hybrid) |
| Codex R2 | Session lifecycle | Explicit SessionStart/SessionEnd, reject out-of-session |
| Codex R2 | Bad data handling | FeedGuard with monotonic/sanity/reconnect checks |
| Codex R2 | Concurrency | Single-writer event loop, immutable snapshots for readers |
| Codex R2 | REST reads live state | Serves EngineSnapshot only (copy-on-write) |
| Codex R2 | Feature trigger policies | TriggerTick / TriggerDepth / TriggerTimer per feature |
| Codex R2 | Warmup too crude | Per-feature Ready() function |
| Codex R2 | SectorState now not later | Built into state hierarchy from day 1 |
| Codex R2 | Quality flags | QualityFlags struct in every output |
| Codex R2 | Config file | features.yaml for all tunable parameters |
| Gemx R2 | SectorState hierarchy | MarketState → SectorState → StockState |
| Codex R2 | Feature dependency caching | VWAP computed once per cycle in handleTick |

---

## What This Plan Does NOT Cover (Future)

- Python screener layer (consumes features from hub)
- Regime classifier integration (governs screener sensitivity)
- Signal generation and alerting
- Dashboard feature visualization
- Candle building (can add later as tick-triggered feature)
- Cross-sectional strength features (% making 5m highs)
- Trend quality features (pullback depth, rotation consistency)
- Historical feature backfill tool (replay ticks → features → CSV)
