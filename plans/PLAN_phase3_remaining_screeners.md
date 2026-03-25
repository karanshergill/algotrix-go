# Phase 3: Port Remaining 4 Screeners

**Goal:** Port Sniper, Trident, ThinMomentum, and TwoSessionHighBreakout screeners from v2 to Go. Same logic, same thresholds.

**Context:** Phase 2 (commit `f33ccf2`) built the screener package with types, engine, DB layer, and EarlyMomentum screener. All new screeners follow the same pattern — implement the `Screener` interface (Name, Evaluate, Reset).

**Reference:** See `engine/screeners/early_momentum.go` for the pattern. Each screener's `Evaluate` method reads from `ctx.Features` map and returns `*Signal` or nil.

---

## Task 1: Port Sniper Screener

**Create:** `engine/screeners/sniper.go`

**Exact v2 thresholds (DO NOT CHANGE):**
- min_spike_ratio: 2.0
- min_buy_ratio: 0.65
- min_total_volume: 5000
- min_gap_pct: 0.1
- max_gap_pct: 3.0
- min_signal_time: 10:00 IST (hour >= 10)
- max_exhaustion: 0.5
- min_book_imbalance: 0.55
- require_above_vwap: true
- Signal type: BUY

**Filter chain (ALL must pass):**
1. `change_pct` between 0.1 and 3.0 (gap filter — different range than EarlyMomentum!)
2. Time >= 10:00 IST: `ctx.TickTime.Hour() >= 10`
3. `volume_spike_ratio` >= 2.0
4. `buy_pressure_5m` >= 0.65
5. `classified_volume_5m` >= 5000
6. `vwap_dist_bps` > 0 (above VWAP — positive means above)
7. `exhaustion` < 0.5
8. `book_imbalance` >= 0.55

**Metadata:** screener, volume_spike_ratio, buy_ratio, classified_volume, change_pct, vwap_dist_bps, exhaustion, book_imbalance

---

## Task 2: Port Trident Screener

**Create:** `engine/screeners/trident.go`

**Exact v2 thresholds (DO NOT CHANGE):**
All Sniper thresholds PLUS:
- max_vwap_dist_bps: 150 (v2 uses ratio 1.015 → bps = 150)
- reject_hours: [11] (no signals during 11:00-11:59 IST)
- max_spike_ratio: 5.0
- Signal type: BUY

**Filter chain: ALL Sniper filters PLUS:**
9. `vwap_dist_bps` <= 150 (not too far above VWAP)
10. `ctx.TickTime.Hour()` NOT in reject_hours (hour != 11)
11. `volume_spike_ratio` < 5.0 (reject abnormal spikes)

**Metadata includes exit params:**
```go
"exit_params": map[string]interface{}{
    "target_pct":           1.0,
    "hard_stop_pct":        2.0,
    "trail_activation_pct": 0.3,
    "trail_distance_pct":   0.35,
    "sqoff_time":           "15:20",
},
```

**Implementation tip:** Trident can embed or call Sniper's checks internally (composition), or just duplicate the filter chain. Duplication is simpler and clearer for a straight port.

---

## Task 3: Port ThinMomentum Screener

**Create:** `engine/screeners/thin_momentum.go`

**Exact v2 thresholds (DO NOT CHANGE):**
- min_spike_ratio: 1.5 (relaxed)
- min_buy_ratio: 0.60 (relaxed)
- min_total_volume: 500 (much lower)
- min_change_pct: 0.5
- max_change_pct: 3.0
- min_book_buy_ratio: 0.60
- min_price: 100
- max_price: 2000
- min_confirming_ticks: 3
- warmup_ticks: 20
- Signal type: ALERT

**Unique behaviors (not in other screeners):**

1. **Warmup:** Track `tickCount map[string]int`. Skip evaluation until `tickCount[ISIN] >= 20`.

2. **Confirming ticks:** Track `confirmingTicks map[string]int`. When ALL conditions pass, increment. When ANY fail, reset to 0. Fire signal only when `confirmingTicks[ISIN] >= 3`.

3. **Spike OR book override:** Volume spike check is relaxed:
   `volume_spike_ratio >= 1.5 OR book_imbalance >= 0.70`

4. **Price range filter:** `ctx.LTP >= 100 && ctx.LTP <= 2000`

**Filter chain:**
1. Price in range (₹100–₹2000)
2. `change_pct` between 0.5 and 3.0
3. `buy_pressure_5m` >= 0.60
4. `classified_volume_5m` >= 500
5. (`volume_spike_ratio` >= 1.5 OR `book_imbalance` >= 0.70)
6. `book_imbalance` >= 0.60
7. Warmup check (>= 20 ticks)
8. If all pass: increment confirmingTicks; if >= 3 → fire signal
9. If any fail: reset confirmingTicks to 0

**Reset():** Clear tickCount and confirmingTicks maps.

---

## Task 4: Port TwoSessionHighBreakout Screener

**Create:** `engine/screeners/breakout.go`

**Exact v2 thresholds (DO NOT CHANGE):**
- lookback_sessions: 2
- min_percent_above: 0.0
- first_break_only: true (handled by engine dedup)
- require_volume_spike: 1.5
- max_exhaustion: 0.75
- require_above_vwap: true
- max_rejection_wick_pct: 2.0
- Signal type: BREAKOUT

**Unique behavior — crossover detection:**
Fire ONLY on actual crossover: `ctx.PrevLTP > 0 && ctx.PrevLTP <= threshold && ctx.LTP > threshold`
First tick (PrevLTP == 0) does NOT fire.

**Threshold data:** Loaded at startup from `algotrix.daily_session_extremes` table:
```sql
SELECT sm.isin, dse.high_value
FROM daily_session_extremes dse
JOIN scrip_master sm ON sm.security_id = dse.security_id
WHERE dse.indicator = 'price'
  AND dse.lookback_sessions = 2
  AND dse.session_date = $1
  AND dse.high_value IS NOT NULL
```

Store as `thresholds map[string]float64` (ISIN → N-session high price).

**Struct fields:**
```go
type BreakoutScreener struct {
    Thresholds          map[string]float64 // ISIN → 2-session high
    RequireVolSpike     float64
    MaxExhaustion       float64
    RequireAboveVWAP    bool
    MaxRejectionWickPct float64
}
```

**Constructor:** `NewBreakoutScreener(thresholds map[string]float64)` — pass thresholds loaded from DB at startup.

**Filter chain:**
1. Threshold exists for this ISIN
2. Crossover: `ctx.PrevLTP > 0 && ctx.PrevLTP <= threshold && ctx.LTP > threshold`
3. Market regime: `ctx.Market.NiftyLTP > ctx.Market.NiftyPrevClose` (bullish)
4. `exhaustion` < 0.75
5. `vwap_dist_bps` > 0 (above VWAP)
6. `volume_spike_ratio` >= 1.5

**Metadata:** screener, threshold_price, percent_above, volume_spike, exhaustion, vwap_dist_bps

---

## Task 5: Write Tests

**Create:** `engine/screeners/sniper_test.go`
- TestSniperAllPass, TestSniperBeforeTime, TestSniperBelowVWAP, TestSniperHighExhaustion

**Create:** `engine/screeners/trident_test.go`
- TestTridentAllPass, TestTridentRejectHour11, TestTridentVWAPTooHigh, TestTridentSpikeTooBig

**Create:** `engine/screeners/thin_momentum_test.go`
- TestThinMomentumConfirmingTicks (3 consecutive passes needed)
- TestThinMomentumWarmup (skip first 20 ticks)
- TestThinMomentumPriceRange
- TestThinMomentumSpikeOrBook

**Create:** `engine/screeners/breakout_test.go`
- TestBreakoutCrossover (prevLTP below, LTP above → signal)
- TestBreakoutNoCrossover (both above → no signal)
- TestBreakoutFirstTick (PrevLTP == 0 → no signal)
- TestBreakoutNoThreshold (ISIN not in thresholds → no signal)

---

## Task 6: Build and Test

```bash
cd /home/me/projects/algotrix-go/engine
go build ./screeners/...
go test ./screeners/ -v
```

---

## Task 7: Commit

```bash
cd /home/me/projects/algotrix-go
git add engine/screeners/
git commit -m "feat(screeners): port Sniper, Trident, ThinMomentum, Breakout from v2

- sniper.go: BUY signal with VWAP/exhaustion/book guards + time gate
- trident.go: BUY signal with VWAP ceiling, hour reject, spike cap + exit params
- thin_momentum.go: ALERT for small-caps with confirming ticks + warmup
- breakout.go: BREAKOUT on 2-session high crossover with confirmation filters
- All thresholds exactly match algotrix-v2"
```

When completely finished, run this command to notify me:
openclaw system event --text "Done: All 4 remaining screeners ported (Sniper, Trident, ThinMomentum, Breakout)" --mode now
