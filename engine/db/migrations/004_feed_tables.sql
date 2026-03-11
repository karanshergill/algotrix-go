-- Migration 004: live feed tables (ticks + depth)

-- Tick data from DataSocket (OHLCV updates per symbol)
CREATE TABLE IF NOT EXISTS nse_cm_ticks (
  ts          TIMESTAMPTZ   NOT NULL,
  symbol      TEXT          NOT NULL,
  ltp         DOUBLE PRECISION,
  volume      BIGINT,
  open        DOUBLE PRECISION,
  high        DOUBLE PRECISION,
  low         DOUBLE PRECISION,
  prev_close  DOUBLE PRECISION,
  change      DOUBLE PRECISION,
  change_pct  DOUBLE PRECISION
);

SELECT create_hypertable('nse_cm_ticks', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_nse_cm_ticks_symbol_ts ON nse_cm_ticks (symbol, ts DESC);

-- Market depth from TBT feed (order book snapshots)
-- Bids and asks stored as JSONB arrays [{price, qty, orders}, ...]
CREATE TABLE IF NOT EXISTS nse_cm_depth (
  ts           TIMESTAMPTZ   NOT NULL,
  symbol       TEXT          NOT NULL,
  tbq          BIGINT,
  tsq          BIGINT,
  best_bid     DOUBLE PRECISION,
  best_ask     DOUBLE PRECISION,
  best_bid_qty DOUBLE PRECISION,
  best_ask_qty DOUBLE PRECISION,
  bids         JSONB,
  asks         JSONB
);

SELECT create_hypertable('nse_cm_depth', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_nse_cm_depth_symbol_ts ON nse_cm_depth (symbol, ts DESC);
