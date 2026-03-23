-- Index constituents table for sector mapping.
-- Stores which ISINs belong to which Nifty sector index.
-- The feature engine reads this at startup; if empty, it falls back to a
-- hardcoded static mapping.

CREATE TABLE IF NOT EXISTS index_constituents (
    index_name TEXT    NOT NULL,
    isin       TEXT    NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (index_name, isin)
);

CREATE INDEX IF NOT EXISTS idx_index_constituents_isin
    ON index_constituents (isin);

COMMENT ON TABLE index_constituents IS
    'Nifty sector index constituents — populated from NSE data, read by the feature engine at startup.';
