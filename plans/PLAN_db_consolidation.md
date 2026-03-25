# PLAN: Database Consolidation (algotrix → atdb)

**Goal:** Single clean database (`atdb`). Retire `algotrix` DB entirely.
**Status:** DRAFT — awaiting approval
**Risk:** LOW — small tables, code changes are DSN swaps + column renames

---

## Current State

- **atdb** — 85 GB, 38 tables — Go engine (ticks, depth, bhavcopy, symbols, features, regime)
- **algotrix** — 90 GB, 54 tables — v2 legacy (signals, scrip_master, tick_data, historical OHLCV)

**Problem:** Go screener code connects to BOTH databases. Signals page needs separate pg.Pool. Two DSNs, confusing, fragile.

---

## Migration Steps (7 steps, sequential)

### Step 1: Migrate `signals` table to atdb
**What:** Recreate signals table in atdb using ISIN as key (not security_id)
**Why:** Go engine uses ISIN everywhere. Eliminates scrip_master FK.
**Schema change:**
- Drop `security_id` → replace with `isin TEXT NOT NULL`
- Drop `config_version_id` FK (v2 legacy, unused by Go)
- Copy existing ~2,229 rows with ISIN join

**Code changes:**
- `engine/screeners/db.go` — Use ISIN directly, remove scrip_master loading
- `engine/screeners/setup.go` — Accept atdb pool, not algotrix DSN
- `server/routes/signals.ts` — Remove algotrixPool, use main pool
- `engine/main.go:277` — Remove algotrixDSN, pass atdb pool

### Step 2: Migrate `daily_session_extremes` to atdb
**What:** Recreate as simple table (not partitioned) with ISIN key
**Why:** Only 37,746 rows, 6MB. Breakout screener needs this.
**Schema change:**
- Drop partition (only 1 partition anyway: `price`)
- Replace `security_id` → `isin TEXT NOT NULL`
- Keep: session_date, indicator, lookback_sessions, high_value, low_value

**Code changes:**
- `engine/screeners/loader.go` — Remove JOIN to scrip_master, query ISIN directly
- Python session_extremes script — write ISIN, target atdb

### Step 3: Kill `scrip_master` dependency
**What:** Remove all scrip_master references. `symbols` table already has everything.
**Why:** `symbols` has dhan_token (= security_id) + Fyers tokens + sectors + is_tradeable.
**Mapping:** `scrip_master.security_id` = `symbols.dhan_token`
- Already handled by Steps 1-2 (FK dependencies removed)

### Step 4: Migrate historical OHLCV to atdb
**What:** Copy `historical_ohlcv_5m` (1.5M rows, 229MB) + `historical_ohlcv_day` (240K rows, 49MB)
**Method:** `pg_dump | psql` (simple, ~280MB)
**Note:** Other OHLCV tables (1m, 15m, 25m, 60m) are empty → skip

### Step 5: Handle `tick_data` (85GB) — DECISION NEEDED
18 daily partitions (Feb 24 – Mar 24), Dhan-era raw ticks.
- **Option A:** Keep in algotrix as read-only archive. replay.go keeps fallback.
- **Option B:** Move to atdb as `dhan_tick_data` (85GB copy).
- **Option C:** Export to parquet on disk, drop from DB. Saves 85GB.

**Recommendation:** Option A for now. Only used by replay mode.

### Step 6: Resolve 8 duplicate tables
- backtest_date_results, _picks, _runs — algotrix has 0 rows → DROP
- nse_announcements, block_deals, board_meetings, corporate_actions — atdb is superset → DROP from algotrix
- **nse_insider_trading** — algotrix has 2.8M vs atdb 119K → MERGE into atdb, then DROP

### Step 7: Drop dead tables + finalize
20+ dead/empty tables in algotrix (config_versions, filters, indicators, etc.)
After Steps 1-6: algotrix only has tick_data (if Option A).
- Rename to `algotrix_archive`, revoke writes
- Or DROP entirely if tick_data exported

---

## Code Change Summary

| File | Change |
|------|--------|
| engine/screeners/db.go | Remove scrip_master, use ISIN, accept atdb pool |
| engine/screeners/setup.go | Remove algotrixDSN param |
| engine/screeners/loader.go | Remove scrip_master JOIN |
| engine/main.go | Remove algotrixDSN, pass atdb pool |
| engine/features/replay.go | Keep algotrix fallback (Option A) |
| server/routes/signals.ts | Remove algotrixPool, use main pool |

---

## Execution Order

1. Step 1 (signals) — biggest impact, eliminates second DB in hot path
2. Step 2 (session_extremes) — completes screener migration
3. Step 3 (scrip_master) — cleanup, done by Step 1
4. Step 6 (duplicates) — merge insider_trading, drop rest
5. Step 4 (historical OHLCV) — simple dump/restore
6. Step 5 (tick_data) — deferred decision
7. Step 7 (cleanup) — final

**Estimated time:** 2-3 hours for Steps 1-4+6

---

## Verification Checklist

- [ ] `go build ./...` compiles with zero algotrix DSN (except replay.go)
- [ ] All 22 screener tests pass
- [ ] go-feed starts, screeners init with atdb-only
- [ ] Signals persist to atdb.signals
- [ ] Breakout thresholds load from atdb.session_extremes
- [ ] Dashboard signals page works (single pool)
- [ ] API server has no algotrix connection
- [ ] replay.go fallback still works
