-- NSEIX (NSE International Exchange / GIFT Nifty) overnight data tables
-- Source: https://www.nseix.com/api/daily-reports (token-authenticated CSV)
-- History: July 2023 → present

BEGIN;

-- Overnight futures bhavcopy (FO)
CREATE TABLE IF NOT EXISTS nseix_overnight_fo (
    date              DATE NOT NULL,
    instrument_type   TEXT NOT NULL,       -- FUTIDX, FUTSTK
    symbol            TEXT NOT NULL,       -- e.g. NIFTY, BANKNIFTY
    expiry            DATE NOT NULL,
    open              REAL,
    high              REAL,
    low               REAL,
    close             REAL,
    settlement        REAL,
    prev_settlement   REAL,
    net_change_pct    REAL,
    oi                BIGINT,
    volume            BIGINT,
    num_trades        BIGINT,
    traded_value      REAL,
    fetched_at        TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (date, instrument_type, symbol, expiry)
);

CREATE INDEX IF NOT EXISTS idx_nseix_fo_symbol ON nseix_overnight_fo (symbol, date);

-- Overnight options bhavcopy (OP)
CREATE TABLE IF NOT EXISTS nseix_overnight_op (
    date              DATE NOT NULL,
    instrument_type   TEXT NOT NULL,       -- OPTIDX, OPTSTK
    symbol            TEXT NOT NULL,
    expiry            DATE NOT NULL,
    strike            REAL NOT NULL,
    option_type       TEXT NOT NULL,       -- CE, PE
    open              REAL,
    high              REAL,
    low               REAL,
    close             REAL,
    settlement        REAL,
    prev_settlement   REAL,
    net_change_pct    REAL,
    oi                BIGINT,
    volume            BIGINT,
    num_trades        BIGINT,
    underlying_settle REAL,
    notional_value    REAL,
    premium_traded    REAL,
    fetched_at        TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (date, instrument_type, symbol, expiry, strike, option_type)
);

CREATE INDEX IF NOT EXISTS idx_nseix_op_symbol ON nseix_overnight_op (symbol, date);

-- Overnight EWMA volatility
CREATE TABLE IF NOT EXISTS nseix_overnight_vol (
    date                      DATE NOT NULL,
    symbol                    TEXT NOT NULL,
    underlying_close          REAL,
    underlying_prev_close     REAL,
    underlying_log_returns    REAL,
    prev_underlying_vol       REAL,
    current_underlying_vol    REAL,
    underlying_ann_vol        REAL,
    futures_close             REAL,
    futures_prev_close        REAL,
    futures_log_returns       REAL,
    prev_futures_vol          REAL,
    current_futures_vol       REAL,
    futures_ann_vol           REAL,
    applicable_daily_vol      REAL,
    applicable_ann_vol        REAL,
    fetched_at                TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (date, symbol)
);

COMMIT;
