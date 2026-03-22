-- 004_layer1_new_feeds.sql
-- New tables for Layer 1 completion: NSE IX settlement, NSE IX combined OI, global market daily.

CREATE TABLE IF NOT EXISTS nseix_settlement_prices (
    date DATE NOT NULL,
    instrument_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    expiry DATE NOT NULL,
    strike REAL NOT NULL DEFAULT 0,
    option_type TEXT NOT NULL DEFAULT 'FF',
    settlement_price REAL NOT NULL,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (date, instrument_type, symbol, expiry, strike, option_type)
);

CREATE TABLE IF NOT EXISTS nseix_combined_oi (
    date DATE NOT NULL,
    instrument_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    expiry DATE NOT NULL,
    strike REAL NOT NULL DEFAULT 0,
    option_type TEXT NOT NULL DEFAULT 'FF',
    delta REAL,
    oi BIGINT NOT NULL,
    combined_oi BIGINT NOT NULL,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (date, instrument_type, symbol, expiry, strike, option_type)
);

CREATE TABLE IF NOT EXISTS global_market_daily (
    date DATE NOT NULL,
    symbol TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL NOT NULL,
    volume BIGINT,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (date, symbol)
);
