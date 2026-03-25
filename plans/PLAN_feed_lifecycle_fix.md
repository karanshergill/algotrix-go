# Plan: Feed Lifecycle & Universe Filter Fix

**Status:** approved
**Priority:** P0 — dashboard feed indicator broken, blocking UX
**Assignee:** Coder

## Problem

1. API server (`server/routes/feed.ts`) spawns `go-feed` as a child process via `spawn()` and tracks state in-memory. When go-feed is managed by PM2 instead, the API server loses track → dashboard shows "error/disconnected" even though feed is live.

2. The `/api/feed/start` route queries `SELECT fy_symbol FROM symbols WHERE status = 'active'` — returns ALL ~2,400 symbols, exceeding Fyers DataSocket limit (~2,000).

## Architecture Decision (reviewed by Codex + Gemx)

- **PM2 owns go-feed lifecycle** — not the API server
- **go-feed exposes `/healthz` endpoint** on its existing REST server (port 3003)
- **API server queries go-feed's health** instead of tracking child process state
- **`is_tradeable` column on `symbols` table** — pre-computed daily by cron
- **API server sends PM2 commands** for start/stop (not `spawn()`)

## Implementation

### Part 1: Health endpoint in go-feed (Go)

**File:** `engine/features/rest.go`

Add a `/healthz` endpoint to the existing REST mux (port 3003):

```go
mux.HandleFunc("GET /healthz", r.handleHealthz)
```

Response JSON:
```json
{
  "status": "running",
  "pid": 12345,
  "uptime_seconds": 3600,
  "stocks_registered": 691,
  "features_active": 19,
  "screeners_active": 5,
  "last_tick_at": "2026-03-24T14:30:00+05:30",
  "ticks_last_minute": 4500,
  "memory_mb": 460
}
```

The handler needs access to:
- `os.Getpid()` for PID
- Engine start time (store in RESTServer or FeatureEngine)
- `len(engine.Stocks())` for stock count
- Feature/screener counts from engine
- Last tick timestamp (add `lastTickAt atomic.Value` to FeatureEngine, update in `handleTick`)
- Tick counter (add rolling 60s counter)
- `runtime.MemStats` for memory

### Part 2: `is_tradeable` column (SQL migration)

**Database:** `atdb`

```sql
ALTER TABLE symbols ADD COLUMN IF NOT EXISTS is_tradeable BOOLEAN NOT NULL DEFAULT false;

-- Initial population using the working filter:
UPDATE symbols s SET is_tradeable = true
FROM (
  SELECT DISTINCT isin
  FROM nse_cm_bhavcopy
  WHERE trade_date >= CURRENT_DATE - INTERVAL '20 days'
    AND series = 'EQ'
  GROUP BY isin
  HAVING
    MAX(close_price) >= 100
    AND AVG(total_traded_quantity) >= 100000
    AND AVG(turnover) >= 50000000
    AND COUNT(DISTINCT trade_date) >= (
      SELECT COUNT(DISTINCT trade_date) FROM nse_cm_bhavcopy
      WHERE trade_date >= CURRENT_DATE - INTERVAL '20 days'
    )
) q
WHERE s.isin = q.isin AND s.status = 'active';
```

### Part 3: Rewrite `server/routes/feed.ts`

Remove ALL child process management (`spawn()`, `ChildProcess`, `proc`, in-memory state).

**New `/api/feed/status`:** Query `http://127.0.0.1:3003/healthz` with 2s timeout. If reachable → status "connected" with health data. If unreachable → status "disconnected".

**New `/api/feed/start`:** Check healthz first (if already running, return 400). Otherwise exec `pm2 start ecosystem.config.cjs --only go-feed` via child_process.

**New `/api/feed/stop`:** Exec `pm2 stop go-feed` via child_process.

Remove: FeedState interface, state object, recordTick(), all spawn/proc/stdout/stderr handling.

### Part 4: Update go-feed startup SQL (Go)

**File:** `engine/features/startup.go`

Change stock registration query to filter by `is_tradeable`:
```sql
SELECT isin, symbol, fy_symbol FROM symbols WHERE status = 'active' AND is_tradeable = true
```

### Part 5: Update PM2 ecosystem config

**File:** `ecosystem.config.cjs`

The Go binary should read qualified symbols from DB (`WHERE is_tradeable = true`) instead of CLI `--symbols` flag. Remove `--symbols` from PM2 config. This eliminates `active_symbols.txt` maintenance.

If the Go binary's feed command currently requires `--symbols`, update it to:
- If `--symbols` provided → use those (backward compat)
- If no `--symbols` → query DB for `is_tradeable = true` symbols

### Part 6: Daily cron to refresh `is_tradeable`

Pre-market (8:45 AM IST), run:
```sql
UPDATE symbols SET is_tradeable = false;
UPDATE symbols s SET is_tradeable = true
FROM (
  SELECT DISTINCT isin FROM nse_cm_bhavcopy
  WHERE trade_date >= CURRENT_DATE - INTERVAL '20 days' AND series = 'EQ'
  GROUP BY isin
  HAVING MAX(close_price) >= 100
    AND AVG(total_traded_quantity) >= 100000
    AND AVG(turnover) >= 50000000
    AND COUNT(DISTINCT trade_date) >= (
      SELECT COUNT(DISTINCT trade_date) FROM nse_cm_bhavcopy
      WHERE trade_date >= CURRENT_DATE - INTERVAL '20 days'
    )
) q WHERE s.isin = q.isin AND s.status = 'active';
```

This can be a Go subcommand (`algotrix universe-refresh`) or a SQL script called from PM2/cron.

## Files to modify

- `engine/features/rest.go` — Add `/healthz` handler
- `engine/features/engine.go` — Add `lastTickAt`, tick counter, start time fields
- `server/routes/feed.ts` — Remove spawn(), query healthz + PM2 commands
- `engine/features/startup.go` — Filter by `is_tradeable` in stock registration
- `ecosystem.config.cjs` — Remove `--symbols` if Go binary reads from DB

## Migration SQL (run once)

```sql
ALTER TABLE symbols ADD COLUMN IF NOT EXISTS is_tradeable BOOLEAN NOT NULL DEFAULT false;
```

## Testing

1. `curl http://localhost:3003/healthz` — returns health JSON when go-feed running
2. `curl http://localhost:3001/api/feed/status` — reflects go-feed health
3. Dashboard feed icon green when go-feed running via PM2
4. Dashboard "Connect" starts go-feed via PM2
5. Dashboard "Disconnect" stops go-feed via PM2
6. `SELECT COUNT(*) FROM symbols WHERE is_tradeable = true` — ~691

## Out of scope (future)

- Named/saved universe configs (dashboard UI)
- Upstox feed integration
- `nse_cm_ohlcv_5m` table + daily fetch cron
