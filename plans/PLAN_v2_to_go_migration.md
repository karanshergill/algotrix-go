# Plan: AlgoTrix v2 → Go Engine Migration

**Goal:** Replace everything `algotrix` DB + Python v2 does with `algotrix-go` + `atdb`, then decommission the old DB.

**Status:** APPROVED — executing today (2026-03-23)
**Timeline:** Today

## Decisions (from Ricky)
- **Ticks:** Fyers only. Drop Dhan tick recording.
- **Insider trading:** Validate if needed (2.8M rows, 2.7GB, 100K+ inserts/day). Currently NOT consumed by Go engine or dashboard.

---

## What v2 Does Today (Active Components)

### 1. Tick Recorder (RUNNING — PID 2116)
- **What:** Dhan WebSocket → `algotrix.tick_data` (Full mode: LTP + 5-level depth + OI)
- **Broker:** Dhan (subscription-based, unlimited 5-level depth)
- **Tables:** `tick_data` (partitioned by day), `scrip_master`
- **Runtime:** Python, runs 9:00–15:35 IST daily, auto-creates daily partitions

### 2. Live Screener Runner
- **What:** Processes ticks through screener engines (gap-up breakout, 2-session high breakout)
- **Tables:** `screeners`, `signals`, `runner_status`, `filters`
- **Config:** YAML-based screener definitions
- **Runtime:** Python async, consumes ticks from Dhan feed

### 3. Daily Enrichment
- **What:** Post-market SQL that recomputes derived fields on `scrip_master` (ATR%, avg turnover, liquidity tier, volatility regime)
- **Tables:** `scrip_master`, `historical_ohlcv_*`

### 4. Historical OHLCV
- **Tables:** `historical_ohlcv_day` (21MB), `historical_ohlcv_5m` (135MB)
- **Source:** Dhan historical API

### 5. NSE Corporate Data
- **Tables:** `nse_insider_trading` (2.7GB!), `nse_announcements` (8.5MB), `nse_board_meetings`, `nse_corporate_actions`, `nse_block_deals`

### 6. Indicators & Scores
- **Tables:** `indicator_latest`, `daily_stock_scores`, `daily_indicator_n_session_extremes_price`, `market_trend`

---

## What Go Engine Already Has

| Capability | Go Engine | Status |
|---|---|---|
| Symbol master | `symbols` table (9,186 stocks, richer than `scrip_master`) | ✅ Done |
| Fyers feed (TBT + DataSocket) | `nse_cm_ticks`, `nse_cm_depth_5` | ✅ Done |
| Daily OHLCV from feed | `nse_cm_ohlcv_1d`, `nse_cm_ohlcv_1m`, `nse_cm_ohlcv_5s` | ✅ Done |
| NSE bhavcopy fetch | `nse_cm_bhavcopy` (2.77M rows, Oct 2025–present) | ✅ Done |
| NSE market data pipeline | `nse_indices_daily`, `nse_vix_daily`, `nse_fo_bhavcopy`, `nse_fii_dii_participant` | ✅ Done |
| Watchlist builder | `watchlists`, `watchlist_isins` | ✅ Done |
| Backtest engine | `backtest_runs`, `backtest_picks`, `backtest_date_results` | ✅ Done |
| Regime classifier | `regime_ground_truth`, `regime_daily`, `market_regime` | ✅ Done |
| Dashboard | React + Vite (port 5180) | ✅ Done |
| API server | Hono (port 3001) | ✅ Done |

---

## Migration Phases

### Phase 1: Stop Dhan Tick Recording ✅ QUICK WIN
**Action:** Stop v2 tick-recorder. Fyers feed (Go engine) replaces it.
**Tasks:**
- [ ] Stop `tick-recorder.service` (systemd) + disable
- [ ] Verify Go feed is writing to `nse_cm_ticks` + `nse_cm_depth_5`
- [ ] Confirm pre-open collector still works (it uses Fyers, not Dhan)

### Phase 2: Port Live Screeners to Go
**Replace:** v2 screener engine (running in tmux `algotrix-v2`)
- Gap-up breakout screener
- Two-session high breakout screener  
- Volume spike detection
- Market trend indicator (breadth, B/S ratio, VWAP)

**Current v2 capabilities (from live tmux):**
- Volume spikes (>2x avg): tracks top movers in real-time
- Market trend: STRONG_BEAR/BEAR/NEUTRAL/BULL/STRONG_BULL based on breadth %, avg return, VWAP %, B/S ratio
- Signal generation + storage in `signals` table

**Tasks:**
- [ ] Audit v2 screener logic (YAML configs + Python code)
- [ ] Design Go screener interface (plugin pattern matching v2's `Screener` protocol)
- [ ] Port gap-up breakout + 2-session high breakout
- [ ] Port volume spike tracker
- [ ] Port market trend indicator
- [ ] Wire into Go feed's tick stream via hub
- [ ] Create `signals` table in atdb
- [ ] Dashboard: signal display widget

### Phase 3: Port Daily Enrichment
**Replace:** `daily_enrichment.py` updating `scrip_master`
**Tasks:**
- [ ] Port enrichment SQL to work on `symbols` table in atdb
- [ ] Computed fields: ATR%, avg_daily_turnover, liquidity_tier, volatility_regime, category
- [ ] Add `symbol_daily_stats` table or enrichment columns to `symbols`
- [ ] Schedule: Go engine post-market command or cron job

### Phase 4: Corporate Data (VALIDATE FIRST)
**Status:** Something is writing 100K+ insider trading rows/day + corporate actions to algotrix DB.
Source unknown — not in v2 source code, possibly a CF Worker or background job.

**Investigation needed:**
- [ ] Find what process writes `nse_insider_trading` (2.8M rows, 100K/day)
- [ ] Find what writes `nse_corporate_actions`, `nse_announcements`, `nse_board_meetings`, `nse_block_deals`
- [ ] Determine if this data feeds any active system (dashboard, screeners, alerts)
- [ ] Decision: migrate to atdb, archive to parquet, or drop entirely

**Current state:** Go engine + dashboard do NOT read any of these tables.

### Phase 5: Historical Data Coverage Check
**Tasks:**
- [ ] Compare date ranges: algotrix `historical_ohlcv_day` vs atdb `nse_cm_bhavcopy`
- [ ] Backfill any older data if atdb is missing
- [ ] 5-minute OHLCV (135MB in v2): skip if 1m/5s candles in atdb suffice

### Phase 6: Decommission
- [ ] Stop `tick-recorder.service` (Phase 1)
- [ ] Kill tmux `algotrix-v2` session (after Phase 2)
- [ ] Stop corporate data fetcher (after Phase 4)
- [ ] Verify zero connections to algotrix DB
- [ ] Parquet archive: already done (13GB)
- [ ] `DROP DATABASE algotrix;` → frees ~85GB

---

## Disk Space Recovery Plan

| Action | Space Freed |
|---|---|
| Drop `algotrix` DB after migration | ~85 GB |
| Delete Parquet archive (optional, after confirming atdb has everything) | ~13 GB |
| Total recoverable | ~98 GB |

---

## Execution Order (Today)

| # | Phase | Effort | Blocker? |
|---|---|---|---|
| 1 | Stop Dhan tick recorder | 5 min | No |
| 2 | Validate Go feed is capturing correctly | 15 min | No |
| 3 | Investigate corporate data fetcher | 30 min | No |
| 4 | Port screeners to Go | 4-6 hours | Coding — delegate to Coder |
| 5 | Port daily enrichment | 1-2 hours | Coding — delegate to Coder |
| 6 | Historical data coverage check | 15 min | No |
| 7 | Decommission algotrix DB | 10 min | After all above |

## Open Questions for Ricky

1. **Screeners:** Port existing v2 screeners as-is, or redesign from scratch?
2. **Corporate data:** Do you use insider trading / corporate actions data for anything? (Go engine + dashboard don't read it)
3. **5-minute OHLCV:** Skip migration since we have 1m + 5s candles?
