-- Migration 002: NSE Market Data Pipeline tables
-- VIX daily, indices daily, F&O bhavcopy, fetch audit log

-- ============================================================
-- India VIX — daily OHLC
-- Source: https://nsearchives.nseindia.com/content/indices/ind_vix_history.csv
-- ============================================================

CREATE TABLE IF NOT EXISTS nse_vix_daily (
    date        DATE PRIMARY KEY,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL NOT NULL,
    fetched_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- NSE Indices — daily OHLCV for ALL indices
-- Source: https://nsearchives.nseindia.com/content/indices/ind_close_all_{DDMMYYYY}.csv
-- ============================================================

CREATE TABLE IF NOT EXISTS nse_indices_daily (
    date        DATE NOT NULL,
    index       TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL NOT NULL,
    volume      BIGINT,
    turnover    REAL,
    pe          REAL,
    pb          REAL,
    div_yield   REAL,
    fetched_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (date, index)
);

-- ============================================================
-- F&O Bhavcopy — all contracts (options + futures)
-- Source: https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip
-- PK uses instrument_id (FinInstrmId) — F&O contracts have empty ISINs
-- ============================================================

CREATE TABLE IF NOT EXISTS nse_fo_bhavcopy (
    date                DATE NOT NULL,
    biz_date            DATE,
    segment             TEXT NOT NULL,
    source              TEXT,
    instrument_type     TEXT NOT NULL,
    instrument_id       INT NOT NULL,
    isin                TEXT,
    symbol              TEXT NOT NULL,
    series              TEXT,
    expiry              DATE,
    actual_expiry       DATE,
    strike              REAL,
    option_type         TEXT,
    instrument_name     TEXT,
    open                REAL,
    high                REAL,
    low                 REAL,
    close               REAL,
    last                REAL,
    prev_close          REAL,
    underlying          REAL,
    settlement          REAL,
    oi                  BIGINT,
    oi_change           BIGINT,
    volume              BIGINT,
    turnover            REAL,
    num_trades          INT,
    session_id          TEXT,
    lot_size            INT,
    remarks             TEXT,
    reserved1           TEXT,
    reserved2           TEXT,
    reserved3           TEXT,
    reserved4           TEXT,
    fetched_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (date, instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_nse_fo_bhavcopy_symbol ON nse_fo_bhavcopy (symbol, date);
CREATE INDEX IF NOT EXISTS idx_nse_fo_bhavcopy_type ON nse_fo_bhavcopy (instrument_type, date);

-- ============================================================
-- Fetch Log — audit trail for pipeline runs
-- One row per feed per date per run
-- ============================================================

CREATE TABLE IF NOT EXISTS nse_fetch_log (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    feed_name       TEXT NOT NULL,
    status          TEXT NOT NULL,
    error_class     TEXT,
    rows_inserted   INT,
    http_status     INT,
    error_message   TEXT,
    duration_ms     INT,
    retries         INT DEFAULT 0,
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nse_fetch_log_date ON nse_fetch_log (date DESC);
CREATE INDEX IF NOT EXISTS idx_nse_fetch_log_feed ON nse_fetch_log (feed_name, date DESC);
