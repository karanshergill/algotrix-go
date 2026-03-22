"""Pre-Open Feature Extractor — 39 features available before 9:15 AM.

All features use ONLY data available before market open:
  Tier 0: GIFT Nifty overnight session (5 features)
  Tier 1: Previous day EOD (16 features)
  Tier 2: Global overnight — S&P 500, USD/INR (2 features)
  Tier 3: Calendar / context (4 features)
  Tier 4: F&O structure, index breadth, CM microstructure (12 features)
"""

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.db import _read_sql, get_connection
from src.v2_features import (
    _safe_float,
    _zscore,
    _rolling_percentile,
    _fetch_nseix_fo_nifty,
    _fetch_nseix_vol_nifty,
    _fetch_fii_dii_full,
    _fetch_fo_bhavcopy_nifty,
    _fetch_cm_bhavcopy_for_breadth,
    _fetch_nifty_returns,
    _fetch_india_vix,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Monthly expiry helpers
# ---------------------------------------------------------------------------

def _last_thursday_of_month(year: int, month: int) -> date:
    """Compute last Thursday of the given month."""
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    # Walk backwards to Thursday (weekday 3)
    while d.weekday() != 3:
        d -= timedelta(days=1)
    return d


def _get_trading_days(start_date: date, end_date: date) -> list[date]:
    """Get trading days from nse_cm_bhavcopy between start and end (inclusive)."""
    df = _read_sql(
        "SELECT DISTINCT date FROM nse_cm_bhavcopy WHERE date >= %s AND date <= %s ORDER BY date",
        params=[start_date, end_date],
    )
    return [d.date() if hasattr(d, 'date') else d for d in df["date"]]


def _trading_days_until_expiry(target_date: date, expiry_date: date) -> int:
    """Count trading days from target_date to expiry_date (inclusive)."""
    if target_date >= expiry_date:
        return 0
    df = _read_sql(
        "SELECT COUNT(DISTINCT date) as cnt FROM nse_cm_bhavcopy WHERE date > %s AND date <= %s",
        params=[target_date, expiry_date],
    )
    if not df.empty and df.iloc[0]["cnt"] is not None:
        return int(df.iloc[0]["cnt"])
    return max(0, int((expiry_date - target_date).days * 5 / 7))


def _next_monthly_expiry(target_date: date) -> date:
    """Find the next monthly F&O expiry (last Thursday) on or after target_date."""
    exp = _last_thursday_of_month(target_date.year, target_date.month)
    if exp < target_date:
        # Move to next month
        if target_date.month == 12:
            exp = _last_thursday_of_month(target_date.year + 1, 1)
        else:
            exp = _last_thursday_of_month(target_date.year, target_date.month + 1)
    return exp


# ---------------------------------------------------------------------------
# Previous trading day
# ---------------------------------------------------------------------------

def _prev_trading_day(target_date: date) -> date | None:
    """Get the most recent trading day before target_date."""
    df = _read_sql(
        """
        SELECT date FROM nse_indices_daily
        WHERE index = 'Nifty 50' AND date < %s
        ORDER BY date DESC LIMIT 1
        """,
        params=[target_date],
    )
    if df.empty:
        return None
    d = df.iloc[0]["date"]
    return d.date() if hasattr(d, 'date') else d


# ---------------------------------------------------------------------------
# Tier 0: GIFT Nifty Overnight (5 features)
# ---------------------------------------------------------------------------

def _compute_tier0(target_date: date, prev_nifty_close: float | None) -> dict:
    """Compute GIFT Nifty overnight features (1-5)."""
    features = {
        "gift_overnight_gap_pct": None,
        "gift_overnight_range_pct": None,
        "gift_overnight_oi_change_pct": None,
        "gift_overnight_volume_conviction": None,
        "gift_overnight_vol_delta": None,
    }

    nseix_fo = _fetch_nseix_fo_nifty(target_date, lookback_days=90)
    nseix_vol = _fetch_nseix_vol_nifty(target_date, lookback_days=90)

    if nseix_fo.empty:
        return features

    latest = nseix_fo.iloc[-1]
    latest_date = latest["date"]
    # Only use if the NSEIX data is for today (overnight before today's open)
    if hasattr(latest_date, 'date'):
        latest_date = latest_date.date()
    if latest_date != target_date:
        return features

    # 1. gift_overnight_gap_pct
    gift_close = latest.get("close")
    if gift_close is None or pd.isna(gift_close):
        gift_close = latest.get("settlement")
    if gift_close is not None and not pd.isna(gift_close) and prev_nifty_close and prev_nifty_close > 0:
        features["gift_overnight_gap_pct"] = _safe_float(
            (float(gift_close) - prev_nifty_close) / prev_nifty_close * 100
        )

    # 2. gift_overnight_range_pct
    high = latest.get("high")
    low = latest.get("low")
    close = latest.get("close")
    if all(v is not None and not pd.isna(v) for v in [high, low, close]) and float(close) > 0:
        features["gift_overnight_range_pct"] = _safe_float(
            (float(high) - float(low)) / float(close) * 100
        )

    # 3. gift_overnight_oi_change_pct
    if len(nseix_fo) >= 2:
        today_oi = latest.get("oi")
        prev_oi = nseix_fo.iloc[-2].get("oi")
        if all(v is not None and not pd.isna(v) for v in [today_oi, prev_oi]) and float(prev_oi) > 0:
            features["gift_overnight_oi_change_pct"] = _safe_float(
                (float(today_oi) - float(prev_oi)) / float(prev_oi) * 100
            )

    # 4. gift_overnight_volume_conviction (vs 20d avg)
    today_vol = latest.get("volume")
    if today_vol is not None and not pd.isna(today_vol) and len(nseix_fo) > 1:
        vol_series = nseix_fo["volume"].astype(float).dropna()
        window = min(20, len(vol_series) - 1)
        if window > 0:
            avg_vol = vol_series.iloc[-window - 1:-1].mean()
            if avg_vol > 0 and not np.isnan(avg_vol):
                features["gift_overnight_volume_conviction"] = _safe_float(
                    float(today_vol) / avg_vol
                )

    # 5. gift_overnight_vol_delta (EWMA vol change)
    if not nseix_vol.empty and len(nseix_vol) >= 2:
        today_ann = nseix_vol.iloc[-1].get("applicable_ann_vol")
        prev_ann = nseix_vol.iloc[-2].get("applicable_ann_vol")
        if all(v is not None and not pd.isna(v) for v in [today_ann, prev_ann]):
            features["gift_overnight_vol_delta"] = _safe_float(
                float(today_ann) - float(prev_ann)
            )

    return features


# ---------------------------------------------------------------------------
# Tier 1: Previous Day EOD (16 features)
# ---------------------------------------------------------------------------

def _compute_tier1(target_date: date, prev_date: date | None) -> dict:
    """Compute previous-day EOD features (6-21)."""
    features = {
        "prev_nifty_return": None,
        "prev_nifty_return_5d": None,
        "prev_nifty_return_20d": None,
        "prev_vix_close": None,
        "prev_vix_change_pct": None,
        "prev_ad_ratio": None,
        "prev_breadth_turnover_weighted": None,
        "prev_pcr_oi": None,
        "prev_max_pain_distance_pct": None,
        "prev_fii_net_idx_fut": None,
        "prev_fii_net_total": None,
        "prev_dii_net_total": None,
        "prev_fii_options_skew": None,
        "prev_index_divergence_500": None,
        "prev_index_divergence_midcap": None,
        "prev_coincident_regime": None,
    }

    if prev_date is None:
        return features

    # --- Nifty returns (6, 7, 8) ---
    nifty_df = _fetch_nifty_returns(prev_date, lookback_days=60)
    if len(nifty_df) >= 2:
        closes = nifty_df["close"].astype(float)
        features["prev_nifty_return"] = _safe_float(
            (closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2]
        )
        if len(closes) >= 6:
            features["prev_nifty_return_5d"] = _safe_float(
                (closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6]
            )
        if len(closes) >= 21:
            features["prev_nifty_return_20d"] = _safe_float(
                (closes.iloc[-1] - closes.iloc[-21]) / closes.iloc[-21]
            )

    # --- VIX (9, 10) ---
    vix_df = _fetch_india_vix(prev_date, lookback_days=10)
    if not vix_df.empty:
        features["prev_vix_close"] = _safe_float(vix_df.iloc[-1]["vix_close"])
        if len(vix_df) >= 2:
            curr_vix = float(vix_df.iloc[-1]["vix_close"])
            prev_vix = float(vix_df.iloc[-2]["vix_close"])
            if prev_vix > 0:
                features["prev_vix_change_pct"] = _safe_float(
                    (curr_vix - prev_vix) / prev_vix * 100
                )

    # --- Breadth (11, 12) ---
    cm_df = _fetch_cm_bhavcopy_for_breadth(prev_date, lookback_days=5)
    if not cm_df.empty:
        day_data = cm_df[cm_df["date"] == prev_date]
        if not day_data.empty:
            advances = (day_data["close"] > day_data["prev_close"]).sum()
            declines = (day_data["close"] < day_data["prev_close"]).sum()
            total = advances + declines
            if total > 0:
                features["prev_ad_ratio"] = _safe_float(advances / total)

            # Turnover-weighted breadth
            if "traded_value" in day_data.columns:
                tv = day_data["traded_value"].fillna(0).astype(float)
                up_mask = day_data["close"] > day_data["prev_close"]
                total_tv = tv.sum()
                if total_tv > 0:
                    features["prev_breadth_turnover_weighted"] = _safe_float(
                        tv[up_mask].sum() / total_tv
                    )

    # --- PCR and Max Pain (13, 14) ---
    fo_df = _fetch_fo_bhavcopy_nifty(prev_date)
    if not fo_df.empty:
        opts = fo_df[fo_df["instrument_type"].isin(["IDO", "OPTIDX"])]
        if not opts.empty:
            # Near month expiry
            expiries = sorted(opts["expiry"].unique())
            near_expiry = expiries[0] if expiries else None
            if near_expiry is not None:
                near_opts = opts[opts["expiry"] == near_expiry]
                calls = near_opts[near_opts["option_type"] == "CE"]
                puts = near_opts[near_opts["option_type"] == "PE"]
                call_oi = calls["oi"].sum()
                put_oi = puts["oi"].sum()
                if call_oi > 0:
                    features["prev_pcr_oi"] = _safe_float(put_oi / call_oi)

                # Max pain
                underlying = fo_df["underlying"].dropna()
                spot = float(underlying.iloc[0]) if not underlying.empty else None
                if spot and spot > 0:
                    strikes = sorted(near_opts["strike"].unique())
                    if strikes:
                        min_pain = float("inf")
                        max_pain_strike = strikes[0]
                        for s in strikes:
                            pain = 0
                            for _, row in calls.iterrows():
                                pain += max(0, s - float(row["strike"])) * float(row.get("oi", 0))
                            for _, row in puts.iterrows():
                                pain += max(0, float(row["strike"]) - s) * float(row.get("oi", 0))
                            if pain < min_pain:
                                min_pain = pain
                                max_pain_strike = s
                        features["prev_max_pain_distance_pct"] = _safe_float(
                            (spot - float(max_pain_strike)) / spot * 100
                        )

    # --- FII/DII (15, 16, 17, 18) ---
    fii_dii = _fetch_fii_dii_full(prev_date, lookback_days=10)
    if not fii_dii.empty:
        latest_fii = fii_dii.iloc[-1]
        # FII net index futures
        fii_idx_net = float(latest_fii["fii_fut_idx_long"]) - float(latest_fii["fii_fut_idx_short"])
        features["prev_fii_net_idx_fut"] = _safe_float(fii_idx_net)
        # FII total net
        features["prev_fii_net_total"] = _safe_float(
            float(latest_fii["fii_total_long"]) - float(latest_fii["fii_total_short"])
        )
        # DII total net
        features["prev_dii_net_total"] = _safe_float(
            float(latest_fii["dii_total_long"]) - float(latest_fii["dii_total_short"])
        )
        # FII options skew (call - put)
        call_total = float(latest_fii["fii_opt_idx_call_long"]) + float(latest_fii["fii_opt_idx_call_short"])
        put_total = float(latest_fii["fii_opt_idx_put_long"]) + float(latest_fii["fii_opt_idx_put_short"])
        denom = call_total + put_total
        if denom > 0:
            features["prev_fii_options_skew"] = _safe_float(call_total / denom)

    # --- Index divergence (19, 20) ---
    for idx_name, feat_key in [
        ("Nifty 500", "prev_index_divergence_500"),
        ("Nifty Midcap 150", "prev_index_divergence_midcap"),
    ]:
        idx_df = _read_sql(
            """
            SELECT date, close FROM nse_indices_daily
            WHERE index = %s AND date <= %s
            ORDER BY date DESC LIMIT 6
            """,
            params=[idx_name, prev_date],
        )
        if len(idx_df) >= 2 and len(nifty_df) >= 2:
            idx_df = idx_df.sort_values("date")
            idx_ret = (float(idx_df.iloc[-1]["close"]) - float(idx_df.iloc[-2]["close"])) / float(idx_df.iloc[-2]["close"])
            nifty_ret = features["prev_nifty_return"]
            if nifty_ret is not None:
                features[feat_key] = _safe_float(idx_ret - nifty_ret)

    # --- Previous coincident regime (21) ---
    # Join from v2 feature matrix or compute from ground truth components
    features["prev_coincident_regime"] = _compute_prev_coincident_regime(prev_date)

    return features


def _compute_prev_coincident_regime(prev_date: date) -> float | None:
    """Compute previous day's coincident regime label as numeric (Bull=2, Neutral=1, Bear=0)."""
    from src.ground_truth import compute_coincident_truth

    # Get Nifty return
    nifty_df = _read_sql(
        "SELECT date, close FROM nse_indices_daily WHERE index = 'Nifty 50' AND date <= %s ORDER BY date DESC LIMIT 2",
        params=[prev_date],
    )
    if len(nifty_df) < 2:
        return None
    nifty_df = nifty_df.sort_values("date")
    nifty_return = (float(nifty_df.iloc[-1]["close"]) - float(nifty_df.iloc[-2]["close"])) / float(nifty_df.iloc[-2]["close"])

    # Get breadth
    breadth_df = _read_sql(
        """
        SELECT SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as adv,
               SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) as dec
        FROM nse_cm_bhavcopy WHERE date = %s
        """,
        params=[prev_date],
    )
    if breadth_df.empty or breadth_df.iloc[0]["adv"] is None:
        return None
    total = float(breadth_df.iloc[0]["adv"]) + float(breadth_df.iloc[0]["dec"])
    if total == 0:
        return None
    breadth_ratio = float(breadth_df.iloc[0]["adv"]) / total

    # Get VIX change
    vix_df = _read_sql(
        "SELECT date, close FROM nse_indices_daily WHERE index = 'India VIX' AND date <= %s ORDER BY date DESC LIMIT 2",
        params=[prev_date],
    )
    if len(vix_df) < 2:
        return None
    vix_df = vix_df.sort_values("date")
    prev_vix = float(vix_df.iloc[-2]["close"])
    if prev_vix == 0:
        return None
    vix_change_pct = (float(vix_df.iloc[-1]["close"]) - prev_vix) / prev_vix * 100

    # Simplified: use E3 label from regime_ground_truth if available
    gt_df = _read_sql(
        "SELECT coincident_label FROM regime_ground_truth WHERE date = %s",
        params=[prev_date],
    )
    if not gt_df.empty and gt_df.iloc[0]["coincident_label"] is not None:
        label = gt_df.iloc[0]["coincident_label"]
        return {"Trend-Up": 2.0, "Range": 1.0, "Trend-Down": 0.0}.get(label, 1.0)
    return None


# ---------------------------------------------------------------------------
# Tier 2: Global Overnight (2 features)
# ---------------------------------------------------------------------------

def _compute_tier2(target_date: date, sp500_df: pd.DataFrame | None, usdinr_df: pd.DataFrame | None) -> dict:
    """Compute global overnight features (22-23)."""
    from src.global_data import get_sp500_overnight_return, get_usdinr_overnight_change

    features = {
        "sp500_overnight_return": None,
        "usdinr_overnight_change": None,
    }

    if sp500_df is not None:
        features["sp500_overnight_return"] = _safe_float(
            get_sp500_overnight_return(target_date, sp500_df)
        )
    if usdinr_df is not None:
        features["usdinr_overnight_change"] = _safe_float(
            get_usdinr_overnight_change(target_date, usdinr_df)
        )

    return features


# ---------------------------------------------------------------------------
# Tier 3: Calendar / Context (4 features)
# ---------------------------------------------------------------------------

def _compute_tier3(target_date: date, prev_date: date | None) -> dict:
    """Compute calendar/context features (24-27)."""
    features = {
        "day_of_week": float(target_date.weekday()),  # Mon=0, Fri=4
        "days_to_monthly_expiry": None,
        "is_expiry_week": None,
        "prev_day_range_pct": None,
    }

    # Days to monthly expiry
    exp = _next_monthly_expiry(target_date)
    days_to_exp = _trading_days_until_expiry(target_date, exp)
    features["days_to_monthly_expiry"] = float(days_to_exp)
    features["is_expiry_week"] = 1.0 if days_to_exp <= 5 else 0.0

    # Previous day range
    if prev_date is not None:
        nifty_day = _read_sql(
            "SELECT high, low, close FROM nse_indices_daily WHERE index = 'Nifty 50' AND date = %s",
            params=[prev_date],
        )
        if not nifty_day.empty:
            h = float(nifty_day.iloc[0]["high"])
            l = float(nifty_day.iloc[0]["low"])
            c = float(nifty_day.iloc[0]["close"])
            if c > 0:
                features["prev_day_range_pct"] = _safe_float((h - l) / c * 100)

    return features


# ---------------------------------------------------------------------------
# Tier 4: F&O Structure, Index Breadth, CM Microstructure (12 features)
# ---------------------------------------------------------------------------

def _compute_tier4(target_date: date, prev_date: date | None) -> dict:
    """Compute Tier 4 features (28-39) from fo_bhavcopy, indices, cm_bhavcopy."""
    features = {
        "prev_nifty_futures_basis_pct": None,
        "prev_nifty_fut_oi_change_pct": None,
        "prev_nifty_fut_volume_ratio": None,
        "prev_pcr_oi_change": None,
        "prev_max_oi_call_distance_pct": None,
        "prev_max_oi_put_distance_pct": None,
        "prev_midcap_vs_nifty": None,
        "prev_smallcap_vs_nifty": None,
        "prev_bank_vs_nifty": None,
        "prev_defensive_vs_cyclical": None,
        "prev_trade_intensity": None,
        "prev_turnover_top10_share": None,
    }

    if prev_date is None:
        return features

    # ---- F&O features (1-6): from nse_fo_bhavcopy ----
    _compute_fo_features(features, prev_date)

    # ---- Index relative features (7-10): from nse_indices_daily ----
    _compute_index_relative_features(features, prev_date)

    # ---- CM microstructure features (11-12): from nse_cm_bhavcopy ----
    _compute_cm_micro_features(features, prev_date)

    return features


def _compute_fo_features(features: dict, prev_date: date) -> None:
    """Compute F&O-derived features from nse_fo_bhavcopy."""
    from datetime import timedelta

    # Fetch Nifty F&O data for prev_date
    fo_df = _read_sql(
        """
        SELECT instrument_type, option_type, strike, expiry,
               close, oi, volume, underlying
        FROM nse_fo_bhavcopy
        WHERE symbol = 'NIFTY' AND date = %s
        """,
        params=[prev_date],
    )
    if fo_df.empty:
        return

    # Spot price
    underlying = fo_df["underlying"].dropna()
    spot = float(underlying.iloc[0]) if not underlying.empty else None
    if spot is None or spot <= 0:
        return

    # --- Near-month futures ---
    futs = fo_df[fo_df["instrument_type"].isin(["FUTIDX", "IDF"])]
    if not futs.empty:
        near_expiry = sorted(futs["expiry"].unique())[0]
        near_fut = futs[futs["expiry"] == near_expiry]

        # 1. Futures basis %
        fut_close = near_fut["close"].dropna()
        if not fut_close.empty:
            features["prev_nifty_futures_basis_pct"] = _safe_float(
                (float(fut_close.iloc[0]) - spot) / spot * 100
            )

        # 2. Futures OI change % (vs prior day)
        fut_oi_today = near_fut["oi"].sum()
        if fut_oi_today and fut_oi_today > 0:
            prev_day_fo = _read_sql(
                """
                SELECT SUM(oi) as prev_oi FROM nse_fo_bhavcopy
                WHERE symbol = 'NIFTY' AND instrument_type IN ('FUTIDX', 'IDF')
                  AND expiry = %s AND date = (
                    SELECT MAX(date) FROM nse_fo_bhavcopy
                    WHERE symbol = 'NIFTY' AND instrument_type IN ('FUTIDX', 'IDF')
                      AND date < %s
                  )
                """,
                params=[near_expiry, prev_date],
            )
            if not prev_day_fo.empty and prev_day_fo.iloc[0]["prev_oi"] is not None:
                prev_oi = float(prev_day_fo.iloc[0]["prev_oi"])
                if prev_oi > 0:
                    features["prev_nifty_fut_oi_change_pct"] = _safe_float(
                        (float(fut_oi_today) - prev_oi) / prev_oi * 100
                    )

        # 3. Futures volume ratio (vs 20d avg)
        fut_vol_today = near_fut["volume"].sum()
        if fut_vol_today and fut_vol_today > 0:
            lookback_start = prev_date - timedelta(days=40)
            hist_vol = _read_sql(
                """
                SELECT date, SUM(volume) as vol
                FROM nse_fo_bhavcopy
                WHERE symbol = 'NIFTY' AND instrument_type IN ('FUTIDX', 'IDF')
                  AND date >= %s AND date < %s
                GROUP BY date ORDER BY date DESC LIMIT 20
                """,
                params=[lookback_start, prev_date],
            )
            if not hist_vol.empty and len(hist_vol) > 0:
                avg_vol = hist_vol["vol"].astype(float).mean()
                if avg_vol > 0:
                    features["prev_nifty_fut_volume_ratio"] = _safe_float(
                        float(fut_vol_today) / avg_vol
                    )

    # --- Options: PCR change, max OI call/put distance ---
    opts = fo_df[fo_df["instrument_type"].isin(["IDO", "OPTIDX"])]
    if opts.empty:
        return

    expiries = sorted(opts["expiry"].unique())
    near_expiry = expiries[0] if expiries else None
    if near_expiry is None:
        return

    near_opts = opts[opts["expiry"] == near_expiry]
    calls = near_opts[near_opts["option_type"] == "CE"]
    puts = near_opts[near_opts["option_type"] == "PE"]
    call_oi = calls["oi"].sum()
    put_oi = puts["oi"].sum()

    # 4. PCR OI change (today's PCR - yesterday's PCR)
    if call_oi > 0:
        pcr_today = float(put_oi) / float(call_oi)
        prev_pcr_df = _read_sql(
            """
            SELECT
                SUM(CASE WHEN option_type = 'PE' THEN oi ELSE 0 END) as put_oi,
                SUM(CASE WHEN option_type = 'CE' THEN oi ELSE 0 END) as call_oi
            FROM nse_fo_bhavcopy
            WHERE symbol = 'NIFTY'
              AND instrument_type IN ('IDO', 'OPTIDX')
              AND expiry = %s
              AND date = (
                SELECT MAX(date) FROM nse_fo_bhavcopy
                WHERE symbol = 'NIFTY' AND date < %s
              )
            """,
            params=[near_expiry, prev_date],
        )
        if not prev_pcr_df.empty and prev_pcr_df.iloc[0]["call_oi"] is not None:
            prev_call_oi = float(prev_pcr_df.iloc[0]["call_oi"])
            prev_put_oi = float(prev_pcr_df.iloc[0]["put_oi"])
            if prev_call_oi > 0:
                pcr_prev = prev_put_oi / prev_call_oi
                features["prev_pcr_oi_change"] = _safe_float(pcr_today - pcr_prev)

    # 5. Max OI call distance % (resistance wall)
    if not calls.empty and spot > 0:
        max_call_strike = float(calls.loc[calls["oi"].idxmax(), "strike"])
        features["prev_max_oi_call_distance_pct"] = _safe_float(
            (max_call_strike - spot) / spot * 100
        )

    # 6. Max OI put distance % (support wall)
    if not puts.empty and spot > 0:
        max_put_strike = float(puts.loc[puts["oi"].idxmax(), "strike"])
        features["prev_max_oi_put_distance_pct"] = _safe_float(
            (spot - max_put_strike) / spot * 100
        )


def _compute_index_relative_features(features: dict, prev_date: date) -> None:
    """Compute index-relative return features from nse_indices_daily."""
    indices = [
        "Nifty 50", "Nifty Midcap 150", "Nifty Smallcap 250", "Nifty Bank",
        "Nifty FMCG", "Nifty Pharma", "Nifty IT", "Nifty Metal", "Nifty Auto", "Nifty Realty",
    ]
    idx_df = _read_sql(
        """
        SELECT index, date, close FROM nse_indices_daily
        WHERE index = ANY(%s) AND date <= %s
        ORDER BY date DESC
        """,
        params=[indices, prev_date],
    )
    if idx_df.empty:
        return

    # Get 1-day return for each index
    returns = {}
    for idx_name in indices:
        idf = idx_df[idx_df["index"] == idx_name].sort_values("date", ascending=False).head(2)
        if len(idf) >= 2:
            curr = float(idf.iloc[0]["close"])
            prev = float(idf.iloc[1]["close"])
            if prev > 0:
                returns[idx_name] = (curr - prev) / prev

    nifty_ret = returns.get("Nifty 50")
    if nifty_ret is None:
        return

    # 7. Midcap vs Nifty
    if "Nifty Midcap 150" in returns:
        features["prev_midcap_vs_nifty"] = _safe_float(returns["Nifty Midcap 150"] - nifty_ret)

    # 8. Smallcap vs Nifty
    if "Nifty Smallcap 250" in returns:
        features["prev_smallcap_vs_nifty"] = _safe_float(returns["Nifty Smallcap 250"] - nifty_ret)

    # 9. Bank vs Nifty
    if "Nifty Bank" in returns:
        features["prev_bank_vs_nifty"] = _safe_float(returns["Nifty Bank"] - nifty_ret)

    # 10. Defensive vs Cyclical
    defensive = [returns.get(n) for n in ["Nifty FMCG", "Nifty Pharma", "Nifty IT"]]
    cyclical = [returns.get(n) for n in ["Nifty Metal", "Nifty Auto", "Nifty Realty"]]
    defensive = [r for r in defensive if r is not None]
    cyclical = [r for r in cyclical if r is not None]
    if defensive and cyclical:
        features["prev_defensive_vs_cyclical"] = _safe_float(
            np.mean(defensive) - np.mean(cyclical)
        )


def _compute_cm_micro_features(features: dict, prev_date: date) -> None:
    """Compute CM bhavcopy microstructure features."""
    from datetime import timedelta

    # 11. Trade intensity: market-wide (num_trades / volume) normalized vs 20d avg
    lookback_start = prev_date - timedelta(days=40)
    intensity_df = _read_sql(
        """
        SELECT date,
               SUM(num_trades)::float / NULLIF(SUM(volume), 0) as trade_intensity
        FROM nse_cm_bhavcopy
        WHERE date >= %s AND date <= %s
        GROUP BY date ORDER BY date
        """,
        params=[lookback_start, prev_date],
    )
    if not intensity_df.empty:
        intensity_df = intensity_df.dropna(subset=["trade_intensity"])
        if not intensity_df.empty:
            today_val = intensity_df[intensity_df["date"] == prev_date]
            if not today_val.empty:
                curr_intensity = float(today_val.iloc[0]["trade_intensity"])
                # 20d avg (excluding today)
                hist = intensity_df[intensity_df["date"] < prev_date].tail(20)
                if not hist.empty:
                    avg_intensity = hist["trade_intensity"].astype(float).mean()
                    if avg_intensity > 0:
                        features["prev_trade_intensity"] = _safe_float(
                            curr_intensity / avg_intensity
                        )

    # 12. Top-10 turnover share
    tv_df = _read_sql(
        """
        SELECT isin, traded_value
        FROM nse_cm_bhavcopy
        WHERE date = %s AND traded_value IS NOT NULL
        ORDER BY traded_value DESC
        """,
        params=[prev_date],
    )
    if not tv_df.empty and len(tv_df) >= 10:
        total_tv = tv_df["traded_value"].astype(float).sum()
        top10_tv = tv_df.head(10)["traded_value"].astype(float).sum()
        if total_tv > 0:
            features["prev_turnover_top10_share"] = _safe_float(top10_tv / total_tv)


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

def compute_preopen_features(
    target_date: date,
    sp500_df: pd.DataFrame | None = None,
    usdinr_df: pd.DataFrame | None = None,
) -> dict:
    """Extract all 39 features available before market open on target_date.

    Args:
        target_date: The date to predict (features are from before 9:15 AM on this date)
        sp500_df: Pre-loaded S&P 500 DataFrame (optional, loaded if not provided)
        usdinr_df: Pre-loaded USD/INR DataFrame (optional, loaded if not provided)

    Returns:
        dict with 39 feature values
    """
    prev_date = _prev_trading_day(target_date)

    # Get previous Nifty close for GIFT gap calculation
    prev_nifty_close = None
    if prev_date:
        nifty_row = _read_sql(
            "SELECT close FROM nse_indices_daily WHERE index = 'Nifty 50' AND date = %s",
            params=[prev_date],
        )
        if not nifty_row.empty:
            prev_nifty_close = float(nifty_row.iloc[0]["close"])

    # Compute all tiers
    tier0 = _compute_tier0(target_date, prev_nifty_close)
    tier1 = _compute_tier1(target_date, prev_date)
    tier2 = _compute_tier2(target_date, sp500_df, usdinr_df)
    tier3 = _compute_tier3(target_date, prev_date)
    tier4 = _compute_tier4(target_date, prev_date)

    features = {}
    features.update(tier0)
    features.update(tier1)
    features.update(tier2)
    features.update(tier3)
    features.update(tier4)

    return features


# Feature column order (for consistent matrix output)
PREOPEN_FEATURE_COLS = [
    "gift_overnight_gap_pct",
    "gift_overnight_range_pct",
    "gift_overnight_oi_change_pct",
    "gift_overnight_volume_conviction",
    "gift_overnight_vol_delta",
    "prev_nifty_return",
    "prev_nifty_return_5d",
    "prev_nifty_return_20d",
    "prev_vix_close",
    "prev_vix_change_pct",
    "prev_ad_ratio",
    "prev_breadth_turnover_weighted",
    "prev_pcr_oi",
    "prev_max_pain_distance_pct",
    "prev_fii_net_idx_fut",
    "prev_fii_net_total",
    "prev_dii_net_total",
    "prev_fii_options_skew",
    "prev_index_divergence_500",
    "prev_index_divergence_midcap",
    "prev_coincident_regime",
    "sp500_overnight_return",
    "usdinr_overnight_change",
    "day_of_week",
    "days_to_monthly_expiry",
    "is_expiry_week",
    "prev_day_range_pct",
    # Tier 4: F&O structure, index breadth, CM microstructure
    "prev_nifty_futures_basis_pct",
    "prev_nifty_fut_oi_change_pct",
    "prev_nifty_fut_volume_ratio",
    "prev_pcr_oi_change",
    "prev_max_oi_call_distance_pct",
    "prev_max_oi_put_distance_pct",
    "prev_midcap_vs_nifty",
    "prev_smallcap_vs_nifty",
    "prev_bank_vs_nifty",
    "prev_defensive_vs_cyclical",
    "prev_trade_intensity",
    "prev_turnover_top10_share",
]
