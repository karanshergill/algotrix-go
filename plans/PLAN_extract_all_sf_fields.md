# PLAN: Extract All SymbolUpdate (sf) Fields

**Status:** DRAFT — awaiting review
**Priority:** P0 — unlocks book_imbalance fix, exchange VWAP, level-1 depth on ticks
**Scope:** Feed layer + event types + state + PG schema

---

## Problem

`onTickMessage()` only extracts 8 of 21 data fields from the Fyers `sf` (SymbolUpdate) feed. 13 fields are silently discarded, including:
- `tot_buy_qty` / `tot_sell_qty` — needed for `book_imbalance` (Fix 2)
- `bid_price` / `ask_price` — level-1 depth on every tick (improves Quote Rule)
- `avg_trade_price` — exchange VWAP (Fix 5)
- `last_traded_qty` — enables per-trade classification

## What Changes

### Layer 1: Feed — `engine/feed/datasocket.go`

**`onTickMessage()`** — extract all 13 missing fields.

**`TickCallback` signature** — change from positional args to struct:

Current:
```go
type TickCallback func(symbol, isin string, ltp float64, volume int64, ts time.Time)
```

New:
```go
type TickData struct {
    Symbol        string
    ISIN          string
    LTP           float64
    Volume        int64
    TS            time.Time
    TotBuyQty     int64
    TotSellQty    int64
    BidPrice      float64  // level-1 best bid
    AskPrice      float64  // level-1 best ask
    BidSize       int64    // level-1 best bid qty
    AskSize       int64    // level-1 best ask qty
    AvgTradePrice float64  // exchange VWAP
    LastTradedQty int64    // last trade size
    LastTradedTime int64   // epoch seconds (exchange)
    ExchFeedTime  int64    // epoch seconds (exchange)
    OI            int64    // open interest (0 for cash)
    YearHigh      float64  // 52-week high
    YearLow       float64  // 52-week low
    LowerCircuit  float64
    UpperCircuit  float64
}

type TickCallback func(data TickData)
```

Why struct? Current callback has 5 positional args. Adding 15 more is unmaintainable. Struct is cleaner, extensible, zero-cost.

### Layer 2: PG Storage — `engine/feed/pg_writer.go`

**`TickRow`** — add 13 new nullable fields (matching TickData minus timestamps).

**`flushTicks()`** — add new columns to COPY statement.

**DB migration:**
```sql
ALTER TABLE nse_cm_ticks
    ADD COLUMN IF NOT EXISTS tot_buy_qty     BIGINT,
    ADD COLUMN IF NOT EXISTS tot_sell_qty     BIGINT,
    ADD COLUMN IF NOT EXISTS bid_price        DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS ask_price        DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS bid_size         BIGINT,
    ADD COLUMN IF NOT EXISTS ask_size         BIGINT,
    ADD COLUMN IF NOT EXISTS avg_trade_price  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS last_traded_qty  BIGINT,
    ADD COLUMN IF NOT EXISTS oi               BIGINT,
    ADD COLUMN IF NOT EXISTS year_high        DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS year_low         DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS lower_circuit    DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS upper_circuit    DOUBLE PRECISION;
```

Note: `last_traded_time` and `exch_feed_time` skipped from PG for now (we have our own `timestamp`). Can add later for latency analysis.

### Layer 3: Feature Engine — `engine/features/engine.go`

**`TickEvent`** — expand:
```go
type TickEvent struct {
    ISIN          string
    Symbol        string
    LTP           float64
    Volume        int64
    TS            time.Time
    TotBuyQty     int64
    TotSellQty    int64
    BidPrice      float64
    AskPrice      float64
    BidSize       int64
    AskSize       int64
    AvgTradePrice float64
    LastTradedQty int64
    YearHigh      float64
    YearLow       float64
    LowerCircuit  float64
    UpperCircuit  float64
}
```

**`handleTick()`** — update StockState with new fields:
```go
// Total book quantities from sf (more frequent than dp)
s.TotalBidQty = ev.TotBuyQty
s.TotalAskQty = ev.TotSellQty

// Level-1 bid/ask from sf (keeps Quote Rule fresh between dp events)
if ev.BidPrice > 0 {
    s.BidPrices[0] = ev.BidPrice
    s.BidQtys[0] = ev.BidSize
}
if ev.AskPrice > 0 {
    s.AskPrices[0] = ev.AskPrice
    s.AskQtys[0] = ev.AskSize
}

if ev.AvgTradePrice > 0 {
    s.ExchVWAP = ev.AvgTradePrice
}
s.LastTradedQty = ev.LastTradedQty
```

### Layer 4: StockState — `engine/features/state.go`

Add new fields:
```go
ExchVWAP       float64   // exchange-computed VWAP
LastTradedQty  int64     // last trade size
YearHigh       float64   // 52-week high
YearLow        float64   // 52-week low
LowerCircuit   float64   // lower circuit limit
UpperCircuit   float64   // upper circuit limit
```

### Layer 5: FeedAdapter — `engine/features/feed_adapter.go`

Update `AdaptTick()` to accept `feed.TickData` struct and map all fields to `TickEvent`.

### Layer 6: Wiring — `engine/main.go`

Update `recorder.SetOnTick()`:
```go
recorder.SetOnTick(func(data feed.TickData) {
    feAdapter.AdaptTick(data)
})
```

---

## What NOT to Change (in this plan)

- **Feature definitions** — `book_imbalance`, `vwap`, etc. updated in separate fix plans once data flows
- **Depth handling** — `handleDepth()` stays as-is for 5-level depth from dp
- **`ClassifyTick` logic** — already reads `BidPrices[0]`/`AskPrices[0]` which will now be fresh from sf

## sf vs dp Interaction

- sf gives level-1 (best bid/ask) + total book qty on EVERY tick (~10-50/sec)
- dp gives 5-level depth on dedicated connection (~1-5/sec)
- sf updates `BidPrices[0]`/`AskPrices[0]` only; dp zeros and overwrites all 5 levels
- No conflict: dp wins on depth granularity, sf wins on frequency

## File Changes Summary

- `engine/feed/datasocket.go` — new `TickData` struct, extract 15 fields, update `TickCallback`
- `engine/feed/pg_writer.go` — expand `TickRow`, update `flushTicks()` COPY columns
- `engine/features/engine.go` — expand `TickEvent`, update `handleTick()`
- `engine/features/state.go` — add 6 new fields to `StockState`
- `engine/features/feed_adapter.go` — update `AdaptTick()` signature
- `engine/main.go` — update tick callback wiring
- `engine/features/feed_adapter_test.go` — update tests
- **DB migration:** ALTER TABLE nse_cm_ticks (13 new nullable columns)

## Tests

- Update `feed_adapter_test.go` for new TickData struct
- Update `engine_test.go` tick tests to populate new TickEvent fields
- Add test: sf bid/ask → StockState `BidPrices[0]`/`AskPrices[0]`
- Add test: sf tot_buy/sell_qty → StockState `TotalBidQty`/`TotalAskQty`

## Risks

- **sf overwrites dp level-1:** sf tick between dp updates writes `BidPrices[0]`/`AskPrices[0]` but not `[1]-[4]`. Next dp update wipes all 5 cleanly. No stale mix.
- **PG schema migration:** Existing data → NULL for new columns. All nullable, no breakage.
- **Callback signature change:** Breaking change to `TickCallback`. Only 2 callers: `main.go` and `recorder_test.go`.

## Execution Order

1. DB migration (ALTER TABLE)
2. Add `TickData` struct + new `TickRow` fields
3. Update `onTickMessage()` to extract all fields
4. Update `flushTicks()` COPY columns
5. Update `TickEvent` + `handleTick()` + `StockState`
6. Update `FeedAdapter` + `main.go` wiring
7. Update tests
8. Build + test + deploy
