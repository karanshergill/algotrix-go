# Plan: v2 → Go Migration — Step-by-Step

**Status:** APPROVED — executing today (2026-03-23)

---

## Architecture Decision: Keep Python Indicators, Switch Feed

**Why NOT rewrite in Go:**
- 2,500 lines of well-tested, working Python indicator logic
- Rewriting = weeks of work for identical functionality
- Python is fine for indicator computation (not latency-critical)

**What changes:**
- Feed source: Dhan WebSocket → Go Hub WebSocket (port 3002)
- DB target: `algotrix` → `atdb`
- Security ID: Dhan `security_id` → ISIN (Go hub uses ISIN)

**Architecture after migration:**
```
Fyers → Go Engine → atdb (nse_cm_ticks + nse_cm_depth_5)
                  → Hub (ws://127.0.0.1:3002)
                       → Python v2 Indicators + Screeners
                       → Hono API Server → Dashboard
```

---

## Step 1: Stop Dhan Tick Recorder
**Time:** 5 min | **Risk:** None (Go feed is already running)

- [ ] `sudo systemctl stop tick-recorder`
- [ ] `sudo systemctl disable tick-recorder`
- [ ] Verify Go feed still writing to `nse_cm_ticks`

---

## Step 2: Hub Data Gap Analysis
**Time:** 30 min | **Risk:** None (read-only analysis)

The Go hub broadcasts tick + depth events. Check what v2 indicators need vs what's available:

| Field | v2 Needs | Hub Tick | Hub Depth | Gap? |
|---|---|---|---|---|
| ltp | ✅ | ✅ | — | No |
| volume | ✅ | ✅ | — | No |
| prevClose (tick.close) | ✅ | ✅ (prevClose) | — | No |
| open/high/low | ✅ | ✅ | — | No |
| change/changePct | ✅ | ✅ | — | No |
| total_buy_qty | ✅ | — | ✅ (tbq) | **Merge needed** |
| total_sell_qty | ✅ | — | ✅ (tsq) | **Merge needed** |
| bid_price_1 | ✅ | — | ✅ (bestBid) | **Merge needed** |
| ask_price_1 | ✅ | — | ✅ (bestAsk) | **Merge needed** |
| avg_price (VWAP) | ✅ | ❌ | ❌ | **MISSING** |
| security_id (Dhan) | ✅ | ❌ (uses ISIN) | ❌ (uses ISIN) | **Remap needed** |

**Gaps to fill:**
1. `avg_price` / VWAP: Not in Fyers DataSocket. Need to compute from ticks (cumulative volume-weighted price).
2. Tick + Depth merge: v2 expects one unified tick object. Hub sends them as separate events. Python adapter needs to merge.
3. ID mapping: v2 uses Dhan `security_id` everywhere. Switch to ISIN as primary key (matches atdb).

**Tasks:**
- [ ] Check if Fyers DataSocket provides avg_price / VWAP
- [ ] If not, build VWAP computation in the Python adapter layer
- [ ] Design merged tick object that combines hub tick + depth events

---

## Step 3: Build Python Hub Adapter
**Time:** 2-3 hours | **Delegate to:** Coder

New module: `algotrix-v2/src/feed/hub_client.py`

**What it does:**
1. Connects to `ws://127.0.0.1:3002` (Go Hub)
2. Receives tick + depth JSON events
3. Merges them into a unified tick object per ISIN
4. Computes running VWAP per stock (if Fyers doesn't provide it)
5. Maps ISIN → internal reference (replaces Dhan security_id)
6. Calls existing `indicator.update(tick)` for each indicator

**Interface:** Must produce a tick object compatible with existing `LiveIndicator.update(tick)` — same attributes as the current Dhan tick model.

**Key design:**
```python
class HubTick:
    """Unified tick from Go Hub, compatible with existing indicators."""
    isin: str           # replaces security_id as primary key
    security_id: int    # kept for backward compat (mapped from ISIN)
    ltp: float
    volume: int
    open: float
    high: float
    low: float
    close: float        # = prevClose (prev session close)
    change: float
    change_pct: float
    avg_price: float    # VWAP (computed or from feed)
    total_buy_qty: int  # from depth event
    total_sell_qty: int # from depth event
    bid_price_1: float  # from depth event
    ask_price_1: float  # from depth event
```

---

## Step 4: Retarget DB Connections
**Time:** 1 hour | **Delegate to:** Coder

All v2 DB connections currently point to `algotrix` DB. Change to `atdb`.

**Files to update:**
- `src/db/connection.py` — DSN change
- `src/indicators/live/volume_spike/baseline.py` — reads `historical_ohlcv_5m` → needs new source
- `src/indicators/live/exhaustion/indicator.py` — reads `daily_stock_scores` → needs new source
- `src/indicators/*/compute.py` (pre-computed: avg_daily_turnover, daily_atr, session_extremes) — SQL updates
- `src/scrips/loader.py` — reads `scrip_master` → switch to `symbols`

**Table mapping:**
| v2 reads from (algotrix) | New source (atdb) | Notes |
|---|---|---|
| `scrip_master` | `symbols` | More columns, ISIN-keyed |
| `historical_ohlcv_5m` | `nse_cm_ohlcv_1m` or recompute | For volume baselines |
| `historical_ohlcv_day` | `nse_cm_bhavcopy` | Daily OHLCV |
| `daily_stock_scores` | New table or compute on-the-fly | ATR, turnover, etc. |
| `signals` | Create in atdb | Signal storage |
| `runner_status` | Create in atdb | Runner heartbeat |
| `market_trend` | Create in atdb | Grafana dashboards |
| `indicator_latest` | Create in atdb | Latest indicator values |

---

## Step 5: Refactor ID System (security_id → ISIN)
**Time:** 2 hours | **Delegate to:** Coder

v2 uses Dhan `security_id` (integer) everywhere as the stock identifier. Switch to ISIN.

**Scope:**
- All indicator `_snapshots`, `_stocks`, `_trades` dicts: `dict[int, ...]` → `dict[str, ...]`
- Screener evaluations
- Signal model
- DB writes

**Approach:** Search-and-replace `security_id` → `isin` across indicator/screener code. The hub adapter (Step 3) provides both `isin` and a backward-compat `security_id` property to ease the transition.

---

## Step 6: Create Missing atdb Tables
**Time:** 30 min

```sql
-- Signal storage
CREATE TABLE signals (
    id SERIAL PRIMARY KEY,
    session_date DATE NOT NULL,
    isin TEXT NOT NULL,
    symbol TEXT NOT NULL,
    screener TEXT NOT NULL,
    signal_type TEXT,
    trigger_price DOUBLE PRECISION,
    ltp_at_signal DOUBLE PRECISION,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Market trend snapshots (for Grafana)
CREATE TABLE market_trend (
    timestamp TIMESTAMPTZ NOT NULL,
    trend TEXT NOT NULL,
    breadth_pct DOUBLE PRECISION,
    avg_change_pct DOUBLE PRECISION,
    above_vwap_pct DOUBLE PRECISION,
    buy_sell_ratio DOUBLE PRECISION,
    advancing INT,
    declining INT,
    total_stocks INT
);
SELECT create_hypertable('market_trend', 'timestamp');

-- Runner status
CREATE TABLE runner_status (
    id SERIAL PRIMARY KEY,
    scrips_subscribed INT,
    status TEXT,
    last_heartbeat TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Step 7: Volume Spike Baseline Migration
**Time:** 1 hour | **Delegate to:** Coder

Volume spike indicator needs 5-minute OHLCV baselines from `historical_ohlcv_5m`.

**Options:**
- A) Compute baselines from `nse_cm_ohlcv_1m` (aggregate 5x 1-min candles → 5-min)
- B) Compute baselines from `nse_cm_ticks` (aggregate ticks → 5-min candles)
- C) Keep `historical_ohlcv_5m` as a read-only reference

**Decision:** Option A if `nse_cm_ohlcv_1m` has enough history (check coverage).

---

## Step 8: Integration Test
**Time:** 1-2 hours

1. Start Go engine feed (already running)
2. Start refactored Python v2 with hub adapter
3. Verify all 8 indicators receive ticks and compute correctly
4. Verify all 5 screeners evaluate and emit signals
5. Verify signals write to `atdb.signals`
6. Verify market_trend writes to `atdb.market_trend`
7. Compare v2 output (Dhan feed) vs refactored output (Fyers hub) side-by-side for 30 min

---

## Step 9: Decommission
**Time:** 15 min (after multi-day validation)

- [ ] Kill tmux `algotrix-v2` (old Dhan-fed runner)
- [ ] `sudo systemctl disable tick-recorder` (already done in Step 1)
- [ ] Verify zero connections to algotrix DB
- [ ] `DROP DATABASE algotrix;` → **frees ~85GB**
- [ ] Remove Parquet archive if atdb has all needed data

---

## Execution Order (Today)

| Order | Step | Time | Who |
|---|---|---|---|
| 1 | Stop Dhan tick recorder | 5 min | Gxozt |
| 2 | Hub data gap analysis | 30 min | Gxozt |
| 3 | Build Python hub adapter | 2-3 hrs | Coder |
| 4 | Retarget DB connections | 1 hr | Coder (parallel with 3) |
| 5 | Refactor ID system | 2 hrs | Coder (after 3) |
| 6 | Create atdb tables | 30 min | Gxozt (parallel) |
| 7 | Volume baseline migration | 1 hr | Coder |
| 8 | Integration test | 1-2 hrs | Gxozt + Ricky |
| 9 | Decommission | After validation | Gxozt |

**Total estimated: 8-10 hours of work, can overlap steps 3+4+6.**
