"""PostgreSQL read/write helpers for atdb."""

import json
import uuid
from contextlib import contextmanager
from datetime import date
from typing import Any

import pandas as pd
import psycopg2
import psycopg2.extras

from src.config import DB_CONFIG

psycopg2.extras.register_uuid()


def get_connection():
    """Create a new DB connection."""
    return psycopg2.connect(**DB_CONFIG)


@contextmanager
def transaction():
    """Context manager for atomic writes. Commits on success, rolls back on error."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reads — raw data from Go pipeline tables
# ---------------------------------------------------------------------------


def _read_sql(query: str, params=None) -> pd.DataFrame:
    """Execute a read query and return a DataFrame. Manages connection lifecycle."""
    conn = get_connection()
    try:
        return pd.read_sql(query, conn, params=params)
    finally:
        conn.close()


def fetch_nifty_ohlcv(end_date: date, lookback_days: int = 200) -> pd.DataFrame:
    """Fetch Nifty 50 index OHLCV from nse_indices_daily.
    Anti-leakage: only fetches data up to and including end_date.
    """
    return _read_sql(
        """
        SELECT date, open, high, low, close, volume FROM (
            SELECT date, open, high, low, close, volume
            FROM nse_indices_daily
            WHERE index = 'Nifty 50'
              AND date <= %s
            ORDER BY date DESC
            LIMIT %s
        ) sub ORDER BY date ASC
        """,
        params=[end_date, lookback_days],
    )


def fetch_india_vix(end_date: date, lookback_days: int = 10) -> pd.DataFrame:
    """Fetch India VIX close from nse_indices_daily."""
    return _read_sql(
        """
        SELECT date, close as vix_close FROM (
            SELECT date, close
            FROM nse_indices_daily
            WHERE index = 'India VIX'
              AND date <= %s
            ORDER BY date DESC
            LIMIT %s
        ) sub ORDER BY date ASC
        """,
        params=[end_date, lookback_days],
    )


def fetch_cm_bhavcopy(target_date: date, lookback_days: int = 30) -> pd.DataFrame:
    """Fetch CM bhavcopy data for breadth calculations.
    Returns OHLCV for all stocks within the date range up to target_date.
    Anti-leakage: computes start_date in Python, passes as normal bound param.
    """
    from datetime import timedelta
    start_date = target_date - timedelta(days=lookback_days)
    return _read_sql(
        """
        SELECT isin, date, open, high, low, close, prev_close, volume
        FROM nse_cm_bhavcopy
        WHERE date <= %s
          AND date >= %s
        ORDER BY date ASC, isin ASC
        """,
        params=[target_date, start_date],
    )


def fetch_fo_bhavcopy(target_date: date) -> pd.DataFrame:
    """Fetch F&O bhavcopy for NIFTY on target_date for PCR and futures basis."""
    return _read_sql(
        """
        SELECT symbol, instrument_type, option_type, strike, expiry,
               open, high, low, close, oi, volume, underlying
        FROM nse_fo_bhavcopy
        WHERE symbol = 'NIFTY'
          AND date = %s
        """,
        params=[target_date],
    )


def fetch_fii_dii(end_date: date, lookback_days: int = 10) -> pd.DataFrame:
    """Fetch FII/DII participant OI data. Anti-leakage: date <= end_date."""
    return _read_sql(
        """
        SELECT date,
               fii_fut_idx_long, fii_fut_idx_short,
               dii_fut_idx_long, dii_fut_idx_short,
               client_total_long, client_total_short
        FROM (
            SELECT * FROM nse_fii_dii_participant
            WHERE date <= %s
            ORDER BY date DESC
            LIMIT %s
        ) sub ORDER BY date ASC
        """,
        params=[end_date, lookback_days],
    )


def fetch_nseix_settlement(end_date: date, lookback_days: int = 5) -> pd.DataFrame:
    """Fetch GIFT Nifty nearest-month futures settlement price.
    Anti-leakage: date <= end_date. Returns one row per date (nearest expiry).
    """
    return _read_sql(
        """
        SELECT date, settlement_price, expiry FROM (
            SELECT DISTINCT ON (date) date, settlement_price, expiry
            FROM nseix_settlement_prices
            WHERE symbol = 'NIFTY'
              AND instrument_type = 'FUTIDX'
              AND date <= %s
            ORDER BY date, expiry ASC
        ) sub
        ORDER BY date DESC
        LIMIT %s
        """,
        params=[end_date, lookback_days],
    )


def fetch_nseix_oi(end_date: date, lookback_days: int = 5) -> pd.DataFrame:
    """Fetch GIFT Nifty aggregate OI (sum across all FUTIDX contracts).
    Anti-leakage: date <= end_date.
    """
    return _read_sql(
        """
        SELECT date, SUM(combined_oi) as total_oi FROM (
            SELECT date, combined_oi
            FROM nseix_combined_oi
            WHERE symbol = 'NIFTY'
              AND instrument_type = 'FUTIDX'
              AND date <= %s
        ) sub
        GROUP BY date
        ORDER BY date DESC
        LIMIT %s
        """,
        params=[end_date, lookback_days],
    )


def fetch_global_cues(end_date: date, lookback_days: int = 5) -> pd.DataFrame:
    """Fetch S&P 500, DXY, US 10Y close prices. Anti-leakage: date <= end_date.
    Returns pivoted DataFrame with columns: date, sp500, dxy, us10y.
    """
    df = _read_sql(
        """
        SELECT date, symbol, close FROM (
            SELECT date, symbol, close
            FROM global_market_daily
            WHERE date <= %s
            ORDER BY date DESC
            LIMIT %s
        ) sub ORDER BY date ASC
        """,
        params=[end_date, lookback_days * 3],  # 3 symbols
    )
    if df.empty:
        return df
    pivot = df.pivot_table(index="date", columns="symbol", values="close").reset_index()
    rename_map = {"^GSPC": "sp500", "DX-Y.NYB": "dxy", "^TNX": "us10y"}
    pivot = pivot.rename(columns=rename_map)
    pivot = pivot.sort_values("date").tail(lookback_days).reset_index(drop=True)
    return pivot


def fetch_nifty_close_pair(date_d: date) -> tuple[float | None, float | None]:
    """Fetch Nifty 50 close for date_d and the previous trading day.
    Returns (prev_close, today_close) or (None, None) if unavailable.
    """
    df = _read_sql(
        """
        SELECT date, close FROM (
            SELECT date, close FROM nse_indices_daily
            WHERE index = 'Nifty 50' AND date <= %s
            ORDER BY date DESC LIMIT 2
        ) sub ORDER BY date ASC
        """,
        params=[date_d],
    )
    if len(df) < 2:
        return (None, None)
    return (float(df.iloc[0]["close"]), float(df.iloc[1]["close"]))


def fetch_fo_bhavcopy_with_expiry(target_date: date) -> pd.DataFrame:
    """Fetch F&O bhavcopy for NIFTY with expiry info for PCR nearest-expiry logic."""
    return _read_sql(
        """
        SELECT instrument_type, option_type, strike, expiry, oi, close, underlying
        FROM nse_fo_bhavcopy
        WHERE symbol = 'NIFTY'
          AND date = %s
        """,
        params=[target_date],
    )


def check_data_available(target_date: date) -> dict[str, bool]:
    """Check which data sources are available for target_date.
    Returns dict of feed_name -> available.
    """
    checks = {}
    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT COUNT(*) FROM nse_indices_daily WHERE date = %s AND index = 'Nifty 50'",
            [target_date],
        )
        checks["nifty_index"] = cur.fetchone()[0] > 0

        cur.execute(
            "SELECT COUNT(*) FROM nse_indices_daily WHERE date = %s AND index = 'India VIX'",
            [target_date],
        )
        checks["india_vix"] = cur.fetchone()[0] > 0

        cur.execute(
            "SELECT COUNT(*) FROM nse_cm_bhavcopy WHERE date = %s", [target_date]
        )
        checks["cm_bhavcopy"] = cur.fetchone()[0] > 0

        cur.execute(
            "SELECT COUNT(*) FROM nse_fo_bhavcopy WHERE date = %s AND symbol = 'NIFTY'",
            [target_date],
        )
        checks["fo_bhavcopy"] = cur.fetchone()[0] > 0

        cur.execute(
            "SELECT COUNT(*) FROM nse_fii_dii_participant WHERE date = %s",
            [target_date],
        )
        checks["fii_dii"] = cur.fetchone()[0] > 0

        cur.execute(
            "SELECT COUNT(*) FROM nseix_settlement_prices WHERE date = %s AND symbol = 'NIFTY' AND instrument_type = 'FUTIDX'",
            [target_date],
        )
        checks["nseix"] = cur.fetchone()[0] > 0

        cur.execute(
            "SELECT COUNT(*) FROM global_market_daily WHERE date = %s",
            [target_date],
        )
        checks["global_cues"] = cur.fetchone()[0] > 0
    finally:
        conn.close()

    return checks


# ---------------------------------------------------------------------------
# Writes — regime tables (single writer: Python)
# ---------------------------------------------------------------------------


def upsert_features(conn, target_date: date, features: dict[str, Any], run_id: uuid.UUID) -> None:
    """Upsert a row into market_regime_features. Must be called within a transaction."""
    # Filter out underscore-prefixed meta keys — they are provenance helpers, not DB columns
    source_window_start = features.pop("_source_window_start", target_date)
    source_window_end = features.pop("_source_window_end", target_date)
    cols = [k for k in features.keys() if not k.startswith("_")]

    meta_cols = ["run_id", "feature_version", "source_window_start", "source_window_end"]
    all_cols = ["date"] + cols + meta_cols
    placeholders = ", ".join(["%s"] * len(all_cols))
    col_names = ", ".join(all_cols)

    update_set = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in cols + meta_cols
    )

    sql = f"""
        INSERT INTO market_regime_features ({col_names}, computed_at)
        VALUES ({placeholders}, NOW())
        ON CONFLICT (date) DO UPDATE SET {update_set}, computed_at = NOW()
    """

    from src.config import FEATURE_VERSION

    values = [target_date] + [features[c] for c in cols] + [
        run_id,
        FEATURE_VERSION,
        source_window_start,
        source_window_end,
    ]

    cur = conn.cursor()
    cur.execute(sql, values)


def upsert_regime(conn, target_date: date, regime: dict[str, Any], run_id: uuid.UUID) -> None:
    """Upsert a row into market_regime. Must be called within a transaction."""
    from src.config import FEATURE_VERSION, CLASSIFIER_VERSION

    sql = """
        INSERT INTO market_regime (
            date, euclidean_label, euclidean_confidence, euclidean_distances,
            hmm_label, hmm_confidence, hmm_state,
            gmm_label, gmm_confidence, gmm_cluster,
            raw_label, final_label, final_confidence,
            dimension_scores, features_snapshot,
            smoothed, smoothing_reason,
            run_id, classifier_version, feature_version, computed_at
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s,
            %s, %s, %s, NOW()
        )
        ON CONFLICT (date) DO UPDATE SET
            euclidean_label = EXCLUDED.euclidean_label,
            euclidean_confidence = EXCLUDED.euclidean_confidence,
            euclidean_distances = EXCLUDED.euclidean_distances,
            hmm_label = EXCLUDED.hmm_label,
            hmm_confidence = EXCLUDED.hmm_confidence,
            hmm_state = EXCLUDED.hmm_state,
            gmm_label = EXCLUDED.gmm_label,
            gmm_confidence = EXCLUDED.gmm_confidence,
            gmm_cluster = EXCLUDED.gmm_cluster,
            raw_label = EXCLUDED.raw_label,
            final_label = EXCLUDED.final_label,
            final_confidence = EXCLUDED.final_confidence,
            dimension_scores = EXCLUDED.dimension_scores,
            features_snapshot = EXCLUDED.features_snapshot,
            smoothed = EXCLUDED.smoothed,
            smoothing_reason = EXCLUDED.smoothing_reason,
            run_id = EXCLUDED.run_id,
            classifier_version = EXCLUDED.classifier_version,
            feature_version = EXCLUDED.feature_version,
            computed_at = NOW()
    """

    cur = conn.cursor()
    cur.execute(sql, [
        target_date,
        regime.get("euclidean_label"),
        regime.get("euclidean_confidence"),
        json.dumps(regime.get("euclidean_distances")) if regime.get("euclidean_distances") else None,
        regime.get("hmm_label"),
        regime.get("hmm_confidence"),
        regime.get("hmm_state"),
        regime.get("gmm_label"),
        regime.get("gmm_confidence"),
        regime.get("gmm_cluster"),
        regime["raw_label"],
        regime["final_label"],
        regime["final_confidence"],
        regime.get("dimension_scores"),
        json.dumps(regime.get("features_snapshot")) if regime.get("features_snapshot") else None,
        regime.get("smoothed", False),
        regime.get("smoothing_reason"),
        run_id,
        CLASSIFIER_VERSION,
        FEATURE_VERSION,
    ])


def fetch_recent_regimes(end_date: date, n: int = 5) -> pd.DataFrame:
    """Fetch recent regime rows for smoothing context."""
    df = _read_sql(
        """
        SELECT date, raw_label, final_label, dimension_scores,
               euclidean_label, hmm_label, gmm_label, final_confidence
        FROM market_regime
        WHERE date < %s
        ORDER BY date DESC
        LIMIT %s
        """,
        params=[end_date, n],
    )
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)
    return df


def fetch_regime_features_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch regime features for a date range (for validation)."""
    return _read_sql(
        """
        SELECT * FROM market_regime_features
        WHERE date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start_date, end_date],
    )


def fetch_regime_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch regime labels for a date range (for validation)."""
    return _read_sql(
        """
        SELECT * FROM market_regime
        WHERE date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start_date, end_date],
    )


# ---------------------------------------------------------------------------
# Phase 2: Regime scoring tables
# ---------------------------------------------------------------------------


def upsert_ground_truth(conn, row: dict) -> None:
    """Upsert into regime_ground_truth."""
    sql = """
        INSERT INTO regime_ground_truth (date, nifty_return, breadth_ratio, vix_change_pct,
                                         coincident_label, next_day_return, predictive_label)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (date) DO UPDATE SET
            nifty_return = EXCLUDED.nifty_return,
            breadth_ratio = EXCLUDED.breadth_ratio,
            vix_change_pct = EXCLUDED.vix_change_pct,
            coincident_label = EXCLUDED.coincident_label,
            next_day_return = EXCLUDED.next_day_return,
            predictive_label = EXCLUDED.predictive_label
    """
    cur = conn.cursor()
    cur.execute(sql, [
        row["date"], row["nifty_return"], row["breadth_ratio"], row["vix_change_pct"],
        row["coincident_label"], row.get("next_day_return"), row.get("predictive_label"),
    ])


def upsert_backtest(conn, row: dict) -> None:
    """Upsert into regime_backtest."""
    sql = """
        INSERT INTO regime_backtest (date, vol_score, trend_score, participation_score,
                                     sentiment_score, institutional_flow_score, composite_score,
                                     regime_label, predicted_label, predicted_confidence,
                                     coincident_truth, predictive_truth,
                                     availability_regime, missing_indicators)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (date) DO UPDATE SET
            vol_score = EXCLUDED.vol_score,
            trend_score = EXCLUDED.trend_score,
            participation_score = EXCLUDED.participation_score,
            sentiment_score = EXCLUDED.sentiment_score,
            institutional_flow_score = EXCLUDED.institutional_flow_score,
            composite_score = EXCLUDED.composite_score,
            regime_label = EXCLUDED.regime_label,
            predicted_label = EXCLUDED.predicted_label,
            predicted_confidence = EXCLUDED.predicted_confidence,
            coincident_truth = EXCLUDED.coincident_truth,
            predictive_truth = EXCLUDED.predictive_truth,
            availability_regime = EXCLUDED.availability_regime,
            missing_indicators = EXCLUDED.missing_indicators
    """
    cur = conn.cursor()
    cur.execute(sql, [
        row["date"], row.get("vol_score"), row.get("trend_score"),
        row.get("participation_score"), row.get("sentiment_score"),
        row.get("institutional_flow_score"), row.get("composite_score"),
        row.get("regime_label"), row.get("predicted_label"),
        row.get("predicted_confidence"), row.get("coincident_truth"),
        row.get("predictive_truth"), row.get("availability_regime"),
        json.dumps(row.get("missing_indicators")) if row.get("missing_indicators") else None,
    ])


def upsert_regime_daily(conn, row: dict) -> None:
    """Upsert into regime_daily."""
    sql = """
        INSERT INTO regime_daily (date, vol_score, trend_score, participation_score,
                                  sentiment_score, institutional_flow_score, composite_score,
                                  regime_label, predicted_next_label, predicted_confidence,
                                  availability_regime, missing_indicators, computed_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
        ON CONFLICT (date) DO UPDATE SET
            vol_score = EXCLUDED.vol_score,
            trend_score = EXCLUDED.trend_score,
            participation_score = EXCLUDED.participation_score,
            sentiment_score = EXCLUDED.sentiment_score,
            institutional_flow_score = EXCLUDED.institutional_flow_score,
            composite_score = EXCLUDED.composite_score,
            regime_label = EXCLUDED.regime_label,
            predicted_next_label = EXCLUDED.predicted_next_label,
            predicted_confidence = EXCLUDED.predicted_confidence,
            availability_regime = EXCLUDED.availability_regime,
            missing_indicators = EXCLUDED.missing_indicators,
            computed_at = NOW()
    """
    cur = conn.cursor()
    cur.execute(sql, [
        row["date"], row.get("vol_score"), row.get("trend_score"),
        row.get("participation_score"), row.get("sentiment_score"),
        row.get("institutional_flow_score"), row.get("composite_score"),
        row["regime_label"], row.get("predicted_next_label"),
        row.get("predicted_confidence"), row.get("availability_regime"),
        json.dumps(row.get("missing_indicators")) if row.get("missing_indicators") else None,
    ])


def fetch_backtest_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch backtest results for evaluation."""
    return _read_sql(
        """
        SELECT * FROM regime_backtest
        WHERE date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start_date, end_date],
    )


def fetch_ground_truth_range(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch ground truth labels for evaluation."""
    return _read_sql(
        """
        SELECT * FROM regime_ground_truth
        WHERE date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start_date, end_date],
    )


def fetch_all_trading_dates() -> list[date]:
    """Get all trading dates from nse_indices_daily (Nifty 50), ordered ASC."""
    df = _read_sql(
        """
        SELECT DISTINCT date FROM nse_indices_daily
        WHERE index = 'Nifty 50'
        ORDER BY date ASC
        """
    )
    return df["date"].tolist()
