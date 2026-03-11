-- Migration 001: full atdb schema
-- NSE Cash Market (CM) — algotrix-go

-- ============================================================
-- Extensions
-- ============================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================
-- Symbols
-- Single source of truth for all NSE CM instruments.
-- isin is the primary key used across all other tables.
-- ============================================================

CREATE TABLE IF NOT EXISTS symbols (
  -- Identity
  isin                  TEXT        NOT NULL PRIMARY KEY,
  symbol                TEXT        NOT NULL UNIQUE,
  name                  TEXT        NOT NULL,
  fy_token              BIGINT      NOT NULL,
  fy_symbol             TEXT        NOT NULL UNIQUE,
  series                TEXT        NOT NULL,

  -- Status
  status                TEXT        NOT NULL DEFAULT 'active',   -- 'active' | 'skipped'
  skip_reason           TEXT,                                    -- why it was skipped
  skip_detail           TEXT,

  -- Timestamps
  created_at            TIMESTAMPTZ DEFAULT now(),
  updated_at            TIMESTAMPTZ DEFAULT now(),

  -- Enrichment — company info
  company_name          TEXT,
  face_value            NUMERIC,
  sector_macro          TEXT,
  sector                TEXT,
  industry              TEXT,
  industry_basic        TEXT,
  listing_date          DATE,
  index_membership      TEXT[],

  -- Enrichment — flags
  is_fno                BOOLEAN     DEFAULT false,
  is_sme                BOOLEAN     DEFAULT false,
  is_psu                BOOLEAN     DEFAULT false,
  promoter_pledged      BOOLEAN     DEFAULT false,

  -- Enrichment — market data
  market_cap            BIGINT,
  free_float_market_cap BIGINT,
  total_shares          BIGINT,
  pe_symbol             NUMERIC,
  pe_sector             NUMERIC,

  -- Enrichment — shareholding
  promoter_pct          NUMERIC,
  public_pct            NUMERIC,
  fii_pct               NUMERIC,
  dii_pct               NUMERIC,
  mutual_fund_pct       NUMERIC,
  insurance_pct         NUMERIC,
  retail_pct            NUMERIC,
  shareholding_quarter  TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbols_status ON symbols (status);
CREATE INDEX IF NOT EXISTS idx_symbols_series ON symbols (series);

-- ============================================================
-- Exchange Calendar
-- NSE trading days, holidays, and session times.
-- ============================================================

CREATE TABLE IF NOT EXISTS calendar (
  date            DATE        NOT NULL PRIMARY KEY,
  is_trading_day  BOOLEAN     NOT NULL DEFAULT true,
  holiday_name    TEXT,
  pre_open_start  TIME,
  exchange_open   TIME,
  exchange_close  TIME,
  post_close_end  TIME,
  is_muhurat      BOOLEAN     NOT NULL DEFAULT false,
  notes           TEXT,
  updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- Indices
-- NSE index definitions used by the feed and dashboard.
-- ============================================================

CREATE TABLE IF NOT EXISTS indices (
  id          SERIAL      PRIMARY KEY,
  symbol      TEXT        NOT NULL UNIQUE,
  name        TEXT        NOT NULL,
  fy_symbol   TEXT        NOT NULL UNIQUE,
  category    TEXT        NOT NULL,     -- 'broad' | 'sectoral' | 'thematic'
  is_active   BOOLEAN     NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

INSERT INTO indices (symbol, name, fy_symbol, category) VALUES
  ('NSE:NIFTY50-INDEX',          'Nifty 50',            'NSE:NIFTY50-INDEX',          'broad'),
  ('NSE:NIFTYNXT50-INDEX',       'Nifty Next 50',       'NSE:NIFTYNXT50-INDEX',       'broad'),
  ('NSE:NIFTY500-INDEX',         'Nifty 500',           'NSE:NIFTY500-INDEX',         'broad'),
  ('NSE:NIFTYMIDCAP150-INDEX',   'Nifty Midcap 150',    'NSE:NIFTYMIDCAP150-INDEX',   'broad'),
  ('NSE:NIFTYSMALLCAP250-INDEX', 'Nifty Smallcap 250',  'NSE:NIFTYSMALLCAP250-INDEX', 'broad'),
  ('NSE:NIFTYBANK-INDEX',        'Nifty Bank',          'NSE:NIFTYBANK-INDEX',        'sectoral'),
  ('NSE:FINNIFTY-INDEX',         'Nifty Fin Service',   'NSE:FINNIFTY-INDEX',         'sectoral'),
  ('NSE:MIDCPNIFTY-INDEX',       'Nifty Midcap Select', 'NSE:MIDCPNIFTY-INDEX',       'sectoral'),
  ('NSE:NIFTYIT-INDEX',          'Nifty IT',            'NSE:NIFTYIT-INDEX',          'sectoral'),
  ('NSE:NIFTYPHARMA-INDEX',      'Nifty Pharma',        'NSE:NIFTYPHARMA-INDEX',      'sectoral'),
  ('NSE:NIFTYAUTO-INDEX',        'Nifty Auto',          'NSE:NIFTYAUTO-INDEX',        'sectoral'),
  ('NSE:NIFTYMETAL-INDEX',       'Nifty Metal',         'NSE:NIFTYMETAL-INDEX',       'sectoral'),
  ('NSE:NIFTYREALTY-INDEX',      'Nifty Realty',        'NSE:NIFTYREALTY-INDEX',      'sectoral')
ON CONFLICT DO NOTHING;

-- ============================================================
-- OHLCV — Historical price data (TimescaleDB hypertables)
-- isin references symbols(isin). All resolutions share the
-- same schema; table name encodes the resolution.
-- ============================================================

CREATE TABLE IF NOT EXISTS nse_cm_ohlcv_1d (
  isin        TEXT             NOT NULL,
  timestamp   TIMESTAMPTZ      NOT NULL,
  open        DOUBLE PRECISION NOT NULL,
  high        DOUBLE PRECISION NOT NULL,
  low         DOUBLE PRECISION NOT NULL,
  close       DOUBLE PRECISION NOT NULL,
  volume      BIGINT           NOT NULL,
  UNIQUE (isin, timestamp)
);
SELECT create_hypertable('nse_cm_ohlcv_1d', 'timestamp', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_nse_cm_ohlcv_1d_isin ON nse_cm_ohlcv_1d (isin, timestamp DESC);

CREATE TABLE IF NOT EXISTS nse_cm_ohlcv_1m (
  isin        TEXT             NOT NULL,
  timestamp   TIMESTAMPTZ      NOT NULL,
  open        DOUBLE PRECISION NOT NULL,
  high        DOUBLE PRECISION NOT NULL,
  low         DOUBLE PRECISION NOT NULL,
  close       DOUBLE PRECISION NOT NULL,
  volume      BIGINT           NOT NULL,
  UNIQUE (isin, timestamp)
);
SELECT create_hypertable('nse_cm_ohlcv_1m', 'timestamp', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_nse_cm_ohlcv_1m_isin ON nse_cm_ohlcv_1m (isin, timestamp DESC);

CREATE TABLE IF NOT EXISTS nse_cm_ohlcv_5s (
  isin        TEXT             NOT NULL,
  timestamp   TIMESTAMPTZ      NOT NULL,
  open        DOUBLE PRECISION NOT NULL,
  high        DOUBLE PRECISION NOT NULL,
  low         DOUBLE PRECISION NOT NULL,
  close       DOUBLE PRECISION NOT NULL,
  volume      BIGINT           NOT NULL,
  UNIQUE (isin, timestamp)
);
SELECT create_hypertable('nse_cm_ohlcv_5s', 'timestamp', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_nse_cm_ohlcv_5s_isin ON nse_cm_ohlcv_5s (isin, timestamp DESC);

-- ============================================================
-- Live Feed — Tick data (DataSocket) and Market Depth (TBT)
-- isin references symbols(isin).
-- Bids/asks stored as JSONB arrays: [{price, qty, orders}, ...]
-- ============================================================

CREATE TABLE IF NOT EXISTS nse_cm_ticks (
  timestamp   TIMESTAMPTZ      NOT NULL,
  isin        TEXT             NOT NULL,
  ltp         DOUBLE PRECISION,
  volume      BIGINT,
  open        DOUBLE PRECISION,
  high        DOUBLE PRECISION,
  low         DOUBLE PRECISION,
  prev_close  DOUBLE PRECISION,
  change      DOUBLE PRECISION,
  change_pct  DOUBLE PRECISION
);
SELECT create_hypertable('nse_cm_ticks', 'timestamp', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_nse_cm_ticks_isin_timestamp ON nse_cm_ticks (isin, timestamp DESC);

CREATE TABLE IF NOT EXISTS nse_cm_depth (
  timestamp    TIMESTAMPTZ      NOT NULL,
  isin         TEXT             NOT NULL,
  tbq          BIGINT,
  tsq          BIGINT,
  best_bid     DOUBLE PRECISION,
  best_ask     DOUBLE PRECISION,
  best_bid_qty DOUBLE PRECISION,
  best_ask_qty DOUBLE PRECISION,
  bids         JSONB,
  asks         JSONB
);
SELECT create_hypertable('nse_cm_depth', 'timestamp', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_nse_cm_depth_isin_timestamp ON nse_cm_depth (isin, timestamp DESC);
