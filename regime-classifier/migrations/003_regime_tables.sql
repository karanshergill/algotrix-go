-- Migration 003: Regime Classifier tables
-- Feature store + classifier output for market regime detection
-- Writer: Python regime-classifier (single writer rule)
-- Readers: Go/Hono API, dashboard

-- ============================================================
-- market_regime_features — all computed indicators per date
-- One row per trading day, idempotent via ON CONFLICT UPDATE
-- ============================================================

CREATE TABLE IF NOT EXISTS market_regime_features (
    date                     DATE PRIMARY KEY,
    -- Volatility dimension
    india_vix_close          REAL,
    nifty_atr14              REAL,
    nifty_atr_pctile_60d     REAL,
    nifty_bbw                REAL,
    nifty_bbw_pctile_60d     REAL,
    -- Trend dimension
    nifty_adx14              REAL,
    nifty_ema20_distance     REAL,
    nifty_ema20_slope        REAL,
    nifty_above_ema20        BOOLEAN,
    -- Participation dimension
    ad_ratio                 REAL,
    ad_ratio_5d_avg          REAL,
    trin                     REAL,
    universe_pct_above_ema20 REAL,
    nifty50_pct_above_ema20  REAL,
    pct_above_ema_delta_5d   REAL,
    -- Sentiment dimension
    pcr_oi                   REAL,
    fut_basis_pct            REAL,
    -- Experimental
    hurst_exponent           REAL,
    -- Meta / Provenance
    run_id                   UUID NOT NULL,
    feature_version          TEXT NOT NULL,
    source_window_start      DATE NOT NULL,
    source_window_end        DATE NOT NULL,
    computed_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- market_regime — classifier output per date
-- Stores all 3 classifier results + smoothed final label
-- ============================================================

CREATE TABLE IF NOT EXISTS market_regime (
    date                 DATE PRIMARY KEY,
    -- Euclidean classifier (production)
    euclidean_label      TEXT,
    euclidean_confidence REAL,
    euclidean_distances  JSONB,
    -- HMM classifier (shadow)
    hmm_label            TEXT,
    hmm_confidence       REAL,
    hmm_state            INT,
    -- GMM classifier (shadow)
    gmm_label            TEXT,
    gmm_confidence       REAL,
    gmm_cluster          INT,
    -- Final output
    raw_label            TEXT NOT NULL,
    final_label          TEXT NOT NULL,
    final_confidence     REAL NOT NULL,
    dimension_scores     REAL[4],
    features_snapshot    JSONB,
    -- Smoothing
    smoothed             BOOLEAN DEFAULT FALSE,
    smoothing_reason     TEXT,
    -- Meta / Provenance
    run_id               UUID NOT NULL,
    classifier_version   TEXT NOT NULL,
    feature_version      TEXT NOT NULL,
    computed_at          TIMESTAMPTZ DEFAULT NOW()
);
