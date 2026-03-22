-- Phase 2: Regime Scoring Engine tables
-- Migration: 005_phase2_regime_scoring.sql

CREATE TABLE IF NOT EXISTS regime_ground_truth (
    date DATE PRIMARY KEY,
    nifty_return REAL NOT NULL,
    breadth_ratio REAL NOT NULL,
    vix_change_pct REAL NOT NULL,
    coincident_label TEXT NOT NULL,
    next_day_return REAL,
    predictive_label TEXT
);

CREATE TABLE IF NOT EXISTS regime_backtest (
    date DATE PRIMARY KEY,
    vol_score REAL,
    trend_score REAL,
    participation_score REAL,
    sentiment_score REAL,
    institutional_flow_score REAL,
    composite_score REAL,
    regime_label TEXT,
    predicted_label TEXT,
    predicted_confidence REAL,
    coincident_truth TEXT,
    predictive_truth TEXT,
    availability_regime TEXT,
    missing_indicators JSONB
);

CREATE TABLE IF NOT EXISTS regime_daily (
    date DATE PRIMARY KEY,
    vol_score REAL,
    trend_score REAL,
    participation_score REAL,
    sentiment_score REAL,
    institutional_flow_score REAL,
    composite_score REAL,
    regime_label TEXT NOT NULL,
    predicted_next_label TEXT,
    predicted_confidence REAL,
    availability_regime TEXT,
    missing_indicators JSONB,
    computed_at TIMESTAMPTZ DEFAULT NOW()
);
