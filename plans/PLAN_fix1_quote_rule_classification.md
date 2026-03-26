# PLAN: Fix 1 — Quote Rule Classification (V2 Parity)

**Status:** APPROVED — Codex ✅, Gemx ✅, Ricky ✅
**Priority:** P0 — affects ALL screeners
**Scope:** Single function replacement + tests

---

## Problem

`ClassifyTick()` in `engine/features/engine.go` uses the **Tick Rule** (price direction only) to classify volume as buy/sell. V2 Python uses the **Quote Rule** (depth-aware classification). This causes `buy_pressure_5m` and `classified_volume_5m` to diverge from v2 values, producing incorrect screener signals.

## What to Change

### File: `engine/features/engine.go` (~line 485)

**Replace the entire `ClassifyTick` method** with the Quote Rule implementation below.

### Current Code (REMOVE):

```go
// ClassifyTick uses the tick rule to classify volume as buy or sell.
// price > lastLTP → buy, price < lastLTP → sell, equal → use last direction.
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

### New Code (INSERT):

```go
// ClassifyTick classifies volume as buy or sell using the Quote Rule (v2 parity).
//
// When depth is available (BidPrices[0] > 0 && AskPrices[0] > 0):
//   Quote Rule (matches v2 VolumeDirectionIndicator._classify):
//   1. ltp >= ask  → BUY  (buyer lifted the ask)
//   2. ltp <= bid  → SELL (seller hit the bid)
//   3. ltp > mid   → BUY
//   4. ltp < mid   → SELL
//   5. ltp == mid  → use TotalBidQty > TotalAskQty (book pressure tiebreak)
//
// When depth is unavailable (no bid/ask yet):
//   Tick Rule fallback (same as previous behaviour):
//   price > lastLTP → buy, price < lastLTP → sell, equal → repeat last direction
//
// Unclassifiable ticks (direction unknown at start, no depth, no prior direction)
// are skipped rather than defaulting to buy.
func (s *StockState) ClassifyTick(price float64, volumeDelta int64, ts time.Time) {
	bid := s.BidPrices[0]
	ask := s.AskPrices[0]

	var direction int8 // 0 = unknown, 1 = buy, -1 = sell

	if bid > 0 && ask > 0 {
		// Quote Rule — depth is available
		if price >= ask {
			direction = 1
		} else if price <= bid {
			direction = -1
		} else {
			mid := (bid + ask) / 2.0
			if price > mid {
				direction = 1
			} else if price < mid {
				direction = -1
			} else {
				// Exactly at midpoint — v2 parity:
				// buy if total bid qty > total ask qty, else sell.
				if s.TotalBidQty > s.TotalAskQty {
					direction = 1
				} else {
					direction = -1
				}
			}
		}
		s.LastDirection = direction
	} else {
		// Tick Rule fallback (no depth available yet)
		if price > s.LastLTP {
			direction = 1
			s.LastDirection = 1
		} else if price < s.LastLTP {
			direction = -1
			s.LastDirection = -1
		} else {
			direction = s.LastDirection // repeat last known direction
		}
	}

	// Skip unclassifiable tick (no depth, no prior direction, no price change)
	if direction == 0 {
		s.LastLTP = price
		return
	}

	if direction > 0 {
		s.CumulativeBuyVol += volumeDelta
		s.BuyVol5m.Add(ts, volumeDelta)
	} else {
		s.CumulativeSellVol += volumeDelta
		s.SellVol5m.Add(ts, volumeDelta)
	}
	s.LastLTP = price
}
```

## Design Notes

- **No changes to TickEvent, DepthEvent, or the event loop** — depth already populates BidPrices, AskPrices, TotalBidQty, TotalAskQty on StockState via handleDepth()
- Before first depth event: tick rule fallback (graceful degradation)
- After first depth event: Quote Rule kicks in automatically
- Unclassifiable ticks (direction=0 at startup) are skipped, not defaulted to buy
- LastDirection is still updated so the tick rule fallback remains coherent
- Depth can be stale while tick is fresh — acceptable, v2 also classifies off latest available book snapshot

## Required Tests

Add table-driven tests in `engine/features/classify_tick_test.go`:

| # | Scenario | Setup | Expected |
|---|----------|-------|----------|
| 1 | No depth, first unchanged tick | bid=0, ask=0, price=100, lastLTP=100, lastDir=0 | skip (no volume classified) |
| 2 | No depth, uptick | bid=0, ask=0, price=101, lastLTP=100 | BUY |
| 3 | No depth, downtick | bid=0, ask=0, price=99, lastLTP=100 | SELL |
| 4 | Depth, price >= ask | bid=99.5, ask=100.5, price=100.5 | BUY |
| 5 | Depth, price <= bid | bid=99.5, ask=100.5, price=99.5 | SELL |
| 6 | Depth, price > mid | bid=99, ask=101, price=100.5 | BUY |
| 7 | Depth, price < mid | bid=99, ask=101, price=99.5 | SELL |
| 8 | Depth, price == mid, bidQty > askQty | bid=99, ask=101, price=100, totalBid=1000, totalAsk=500 | BUY |
| 9 | Depth, price == mid, bidQty <= askQty | bid=99, ask=101, price=100, totalBid=500, totalAsk=1000 | SELL |

Each test: verify CumulativeBuyVol or CumulativeSellVol incremented correctly (or neither for skip case).

## What NOT to Change

- No changes to state.go (all fields already exist)
- No changes to handleTick() or handleDepth()
- No changes to screener files
- No changes to feature computation functions
- No changes to TickEvent or DepthEvent structs

## Verification

After implementing:
1. `go test ./engine/features/...` — all existing + new tests pass
2. `go build ./...` — clean compile
3. Commit message: `fix(features): replace tick rule with quote rule classification (v2 parity)`

## V2 Reference

Python source: `~/projects/algotrix-v2/src/indicators/live/volume_direction/indicator.py`
Method: `VolumeDirectionIndicator._classify()`
