"""v2 Feature Extraction — 28 features across 8 families.

All features follow the anti-leakage contract: for date D, only data from dates <= D is used.
Each feature documents its hypothesis, source, and normalization.

Families:
  Tier 0:  Overnight State (5 features)  — source: nseix_overnight_fo, nseix_overnight_vol
  Tier 1A: FII/DII Institutional (6 features) — source: nse_fii_dii_participant
  Tier 1B: FO Positioning (5 features)   — source: nse_fo_bhavcopy
  Tier 1C: Global Shock Structure (1 feature) — source: nse_indices_daily (India VIX)
  Tier 2A: CM Breadth Quality (4 features) — source: nse_cm_bhavcopy
  Tier 2B: Volatility Conditioning (2 features) — source: nseix_overnight_vol, nse_indices_daily
  Tier 2C: Index Divergence (4 features)  — source: nse_indices_daily (multi-index)
"""

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.db import _read_sql, get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_trading_days_between(start_date: date, end_date: date) -> int:
    """Count actual trading days between two dates using nse_cm_bhavcopy as source of truth.

    Returns the number of trading days in (start_date, end_date] (exclusive start, inclusive end).
    Falls back to a conservative calendar-day estimate (7 cal days ≈ 5 trading days) on error.
    """
    if start_date >= end_date:
        return 0
    try:
        df = _read_sql(
            """
            SELECT COUNT(DISTINCT date) as cnt
            FROM nse_cm_bhavcopy
            WHERE date > %s AND date <= %s
            """,
            params=[start_date, end_date],
        )
        if not df.empty and df.iloc[0]["cnt"] is not None:
            return int(df.iloc[0]["cnt"])
    except Exception:
        pass
    # Fallback: estimate trading days from calendar days (5/7 ratio)
    cal_days = (end_date - start_date).days
    return max(0, int(cal_days * 5 / 7))


def _trading_days_until(target_date: date, expiry_date: date) -> int:
    """Count trading days from target_date to expiry_date (inclusive of expiry)."""
    return _count_trading_days_between(target_date, expiry_date)


def _safe_float(val) -> float | None:
    """Convert to Python float, returning None for NaN/None."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _zscore(series: pd.Series, window: int = 20) -> float:
    """Rolling z-score of the last value over `window` periods."""
    if len(series) < window:
        return np.nan
    window_vals = series.iloc[-window:]
    mean = window_vals.mean()
    std = window_vals.std()
    if std == 0 or np.isnan(std):
        return 0.0
    return (series.iloc[-1] - mean) / std


def _rolling_percentile(series: pd.Series, window: int = 60) -> float:
    """Rolling percentile rank (0-100) of the last value over `window` periods."""
    if len(series) < window:
        return np.nan
    window_vals = series.iloc[-window:]
    rank = (window_vals < window_vals.iloc[-1]).sum()
    return rank / (len(window_vals) - 1) * 100


# ---------------------------------------------------------------------------
# Data fetchers (NSEIX-specific)
# ---------------------------------------------------------------------------

def _fetch_nseix_fo_nifty(end_date: date, lookback_days: int = 90) -> pd.DataFrame:
    """Fetch near-month NIFTY futures from nseix_overnight_fo.

    Applies expiry selection: nearest monthly expiry, roll to next month
    when current expiry < 3 trading days away.
    """
    start_date = end_date - timedelta(days=lookback_days + 10)
    df = _read_sql(
        """
        SELECT date, instrument_type, symbol, expiry, open, high, low, close,
               settlement, prev_settlement, oi, volume, num_trades, traded_value
        FROM nseix_overnight_fo
        WHERE symbol = 'NIFTY'
          AND instrument_type = 'FUTIDX'
          AND date >= %s AND date <= %s
        ORDER BY date ASC, expiry ASC
        """,
        params=[start_date, end_date],
    )
    if df.empty:
        return df

    # For each date, pick the near-month contract (roll when expiry < 3 trading days away)
    result_rows = []
    for d, group in df.groupby("date"):
        expiries = sorted(group["expiry"].unique())
        if not expiries:
            continue

        nearest = expiries[0]
        trading_days_to_expiry = _trading_days_until(d, nearest)

        # Roll to next month if within 3 trading days of expiry
        if trading_days_to_expiry < 3 and len(expiries) > 1:
            nearest = expiries[1]

        row = group[group["expiry"] == nearest].iloc[0]
        result_rows.append(row)

    return pd.DataFrame(result_rows).reset_index(drop=True) if result_rows else pd.DataFrame()


def _fetch_nseix_vol_nifty(end_date: date, lookback_days: int = 90) -> pd.DataFrame:
    """Fetch NIFTY volatility data from nseix_overnight_vol."""
    start_date = end_date - timedelta(days=lookback_days + 10)
    return _read_sql(
        """
        SELECT date, applicable_ann_vol, current_underlying_vol, underlying_ann_vol
        FROM nseix_overnight_vol
        WHERE symbol = 'NIFTY'
          AND date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start_date, end_date],
    )


def _fetch_fii_dii_full(end_date: date, lookback_days: int = 65) -> pd.DataFrame:
    """Fetch FII/DII participant data with all columns needed for Tier 1A."""
    start_date = end_date - timedelta(days=lookback_days + 10)
    return _read_sql(
        """
        SELECT date,
               fii_fut_idx_long, fii_fut_idx_short,
               fii_fut_stk_long, fii_fut_stk_short,
               fii_opt_idx_call_long, fii_opt_idx_put_long,
               fii_opt_idx_call_short, fii_opt_idx_put_short,
               fii_total_long, fii_total_short,
               dii_total_long, dii_total_short,
               client_total_long, client_total_short
        FROM nse_fii_dii_participant
        WHERE date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start_date, end_date],
    )


def _fetch_fo_bhavcopy_nifty(target_date: date) -> pd.DataFrame:
    """Fetch NIFTY F&O bhavcopy for Tier 1B features."""
    return _read_sql(
        """
        SELECT instrument_type, option_type, strike, expiry,
               open, high, low, close, prev_close, oi, oi_change, volume, underlying
        FROM nse_fo_bhavcopy
        WHERE symbol = 'NIFTY'
          AND date = %s
        """,
        params=[target_date],
    )


def _fetch_fo_bhavcopy_nifty_range(end_date: date, lookback_days: int = 5) -> pd.DataFrame:
    """Fetch NIFTY F&O bhavcopy for a date range (for buildup classification)."""
    start_date = end_date - timedelta(days=lookback_days + 5)
    return _read_sql(
        """
        SELECT date, instrument_type, expiry, close, prev_close, oi, oi_change, volume, underlying
        FROM nse_fo_bhavcopy
        WHERE symbol = 'NIFTY'
          AND instrument_type IN ('IDF', 'FUTIDX')
          AND date >= %s AND date <= %s
        ORDER BY date ASC, expiry ASC
        """,
        params=[start_date, end_date],
    )


def _fetch_cm_bhavcopy_for_breadth(target_date: date, lookback_days: int = 25) -> pd.DataFrame:
    """Fetch CM bhavcopy for Tier 2A breadth features."""
    start_date = target_date - timedelta(days=lookback_days + 10)
    return _read_sql(
        """
        SELECT isin, date, open, high, low, close, prev_close, volume,
               traded_value
        FROM nse_cm_bhavcopy
        WHERE date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start_date, target_date],
    )


def _fetch_nifty_returns(end_date: date, lookback_days: int = 30) -> pd.DataFrame:
    """Fetch Nifty 50 close prices for realized vol calculation."""
    start_date = end_date - timedelta(days=lookback_days + 10)
    return _read_sql(
        """
        SELECT date, close FROM nse_indices_daily
        WHERE index = 'Nifty 50'
          AND date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start_date, end_date],
    )


def _fetch_india_vix(end_date: date, lookback_days: int = 5) -> pd.DataFrame:
    """Fetch India VIX for vol spread calculation."""
    start_date = end_date - timedelta(days=lookback_days + 10)
    return _read_sql(
        """
        SELECT date, open as vix_open, close as vix_close FROM nse_indices_daily
        WHERE index = 'India VIX'
          AND date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start_date, end_date],
    )


# ---------------------------------------------------------------------------
# Tier 0: Overnight State (5 features)
# ---------------------------------------------------------------------------

def _get_monthly_expiries(expiries: list) -> list:
    """Filter to monthly expiries only (last Thursday of month)."""
    # Monthly expiries are typically the last Thursday; we approximate by
    # keeping only the latest expiry per calendar month
    from collections import defaultdict
    by_month = defaultdict(list)
    for e in expiries:
        by_month[(e.year, e.month)].append(e)
    return [max(v) for v in by_month.values()]


def compute_overnight_gap_pct(nseix_fo: pd.DataFrame, nifty_prev_close: float) -> dict:
    """Tier 0: overnight_gap_pct

    Hypothesis: Gap between GIFT Nifty overnight close and NSE's previous close
    reflects global sentiment absorbed overnight. Larger gaps predict stronger
    opening momentum.

    Source: nseix_overnight_fo (near-month NIFTY futures close) vs nse_indices_daily (Nifty 50 close)
    Normalization: raw pct change + 20-day z-score
    """
    if nseix_fo.empty or nifty_prev_close is None or nifty_prev_close == 0:
        return {"overnight_gap_pct": None, "overnight_gap_pct_z20": None}

    latest = nseix_fo.iloc[-1]
    gift_close = latest["close"]
    if gift_close is None or pd.isna(gift_close):
        # Fall back to settlement
        gift_close = latest.get("settlement")
    if gift_close is None or pd.isna(gift_close):
        return {"overnight_gap_pct": None, "overnight_gap_pct_z20": None}

    gap_pct = (float(gift_close) - nifty_prev_close) / nifty_prev_close * 100

    # Compute z-score from historical gaps
    # Gap series: each day's GIFT close vs prior day's GIFT close (approximates
    # overnight gap since GIFT close ≈ next NSE open expectation).
    # Z-score uses prior 20 values (excluding current) so current day's gap
    # is measured against recent history, not included in its own baseline.
    z20 = None
    if len(nseix_fo) >= 21 and "close" in nseix_fo.columns:
        closes = nseix_fo["close"].astype(float).dropna()
        if len(closes) >= 21:
            # Day-over-day pct change as gap proxy
            gap_series = closes.pct_change().dropna() * 100
            if len(gap_series) >= 21:
                # Z-score: current gap vs prior 20 gaps (exclude current from window)
                prior = gap_series.iloc[-(21):-1]
                current_gap = gap_series.iloc[-1]
                mean = prior.mean()
                std = prior.std()
                if std > 0 and not np.isnan(std):
                    z20 = _safe_float((current_gap - mean) / std)

    return {
        "overnight_gap_pct": _safe_float(gap_pct),
        "overnight_gap_pct_z20": z20,
    }


def compute_overnight_range_pct(nseix_fo: pd.DataFrame) -> dict:
    """Tier 0: overnight_range_pct

    Hypothesis: Wider overnight range = higher uncertainty absorbed during global
    session. Predicts opening volatility.

    Source: nseix_overnight_fo (HIGH - LOW) / PREVIOUS_S for near-month NIFTY futures
    Normalization: raw pct, 20-day rolling percentile, 60-day rolling percentile
    """
    if nseix_fo.empty:
        return {"overnight_range_pct": None, "overnight_range_pct_p20": None, "overnight_range_pct_p60": None}

    latest = nseix_fo.iloc[-1]
    high = latest.get("high")
    low = latest.get("low")
    prev_s = latest.get("prev_settlement")

    if any(pd.isna(v) or v is None for v in [high, low, prev_s]) or prev_s == 0:
        return {"overnight_range_pct": None, "overnight_range_pct_p20": None, "overnight_range_pct_p60": None}

    range_pct = (float(high) - float(low)) / float(prev_s) * 100

    # Rolling percentiles
    p20, p60 = None, None
    if len(nseix_fo) >= 5:
        highs = nseix_fo["high"].astype(float)
        lows = nseix_fo["low"].astype(float)
        prevs = nseix_fo["prev_settlement"].astype(float)
        prevs = prevs.replace(0, np.nan)
        range_series = (highs - lows) / prevs * 100
        range_series = range_series.dropna()
        if len(range_series) >= 20:
            p20 = _safe_float(_rolling_percentile(range_series, 20))
        if len(range_series) >= 60:
            p60 = _safe_float(_rolling_percentile(range_series, 60))

    return {
        "overnight_range_pct": _safe_float(range_pct),
        "overnight_range_pct_p20": p20,
        "overnight_range_pct_p60": p60,
    }


def compute_overnight_oi_change_pct(nseix_fo: pd.DataFrame) -> dict:
    """Tier 0: overnight_oi_change_pct

    Hypothesis: OI buildup during overnight session signals positioning conviction.
    Rising OI + price move = strong directional bias.

    Source: nseix_overnight_fo — OI change from consecutive days for near-month NIFTY
    Normalization: pct change from prior day, 20-day z-score
    """
    if len(nseix_fo) < 2:
        return {"overnight_oi_change_pct": None, "overnight_oi_change_pct_z20": None}

    today_oi = nseix_fo.iloc[-1].get("oi")
    prev_oi = nseix_fo.iloc[-2].get("oi")

    if any(v is None or pd.isna(v) for v in [today_oi, prev_oi]) or prev_oi == 0:
        return {"overnight_oi_change_pct": None, "overnight_oi_change_pct_z20": None}

    oi_change_pct = (float(today_oi) - float(prev_oi)) / float(prev_oi) * 100

    z20 = None
    if len(nseix_fo) >= 21:
        oi_series = nseix_fo["oi"].astype(float)
        oi_pct = oi_series.pct_change() * 100
        oi_pct = oi_pct.dropna()
        if len(oi_pct) >= 20:
            z20 = _safe_float(_zscore(oi_pct, 20))

    return {
        "overnight_oi_change_pct": _safe_float(oi_change_pct),
        "overnight_oi_change_pct_z20": z20,
    }


def compute_overnight_volume_conviction(nseix_fo: pd.DataFrame) -> dict:
    """Tier 0: overnight_volume_conviction

    Hypothesis: Higher overnight volume relative to recent average = more participants
    pricing in overnight news. Low volume gaps are more likely to fade.

    Source: nseix_overnight_fo — TRADED_QUA for near-month NIFTY / 20-day SMA
    Normalization: ratio to 20-day average (>1 = above average conviction)
    """
    if nseix_fo.empty:
        return {"overnight_volume_conviction": None}

    today_vol = nseix_fo.iloc[-1].get("volume")
    if today_vol is None or pd.isna(today_vol):
        return {"overnight_volume_conviction": None}

    vol_series = nseix_fo["volume"].astype(float).dropna()
    if len(vol_series) < 2:
        return {"overnight_volume_conviction": None}

    # Use up to 20-day SMA
    window = min(20, len(vol_series) - 1)
    avg_vol = vol_series.iloc[-window - 1:-1].mean()

    if avg_vol == 0 or np.isnan(avg_vol):
        return {"overnight_volume_conviction": None}

    ratio = float(today_vol) / avg_vol

    return {"overnight_volume_conviction": _safe_float(ratio)}


def compute_overnight_vol_delta(nseix_vol: pd.DataFrame) -> dict:
    """Tier 0: overnight_vol_delta

    Hypothesis: Change in EWMA annualized volatility from overnight session vs prior
    day captures volatility regime shifts happening in global hours.

    Source: nseix_overnight_vol — applicable_ann_vol for NIFTY, delta from prior day
    Normalization: raw delta, 20-day z-score of delta
    """
    if len(nseix_vol) < 2:
        return {"overnight_vol_delta": None, "overnight_vol_delta_z20": None}

    today_vol = nseix_vol.iloc[-1].get("applicable_ann_vol")
    prev_vol = nseix_vol.iloc[-2].get("applicable_ann_vol")

    if any(v is None or pd.isna(v) for v in [today_vol, prev_vol]):
        return {"overnight_vol_delta": None, "overnight_vol_delta_z20": None}

    delta = float(today_vol) - float(prev_vol)

    z20 = None
    if len(nseix_vol) >= 21:
        ann_vol = nseix_vol["applicable_ann_vol"].astype(float)
        deltas = ann_vol.diff().dropna()
        if len(deltas) >= 20:
            z20 = _safe_float(_zscore(deltas, 20))

    return {
        "overnight_vol_delta": _safe_float(delta),
        "overnight_vol_delta_z20": z20,
    }


# ---------------------------------------------------------------------------
# Tier 1A: FII/DII Institutional Decomposition (6 features)
# ---------------------------------------------------------------------------

def compute_fii_net_idx_fut(fii_dii: pd.DataFrame) -> dict:
    """Tier 1A: fii_net_idx_fut

    Hypothesis: FII net index futures position (long - short) is the strongest single
    institutional signal for Nifty direction. Net long = bullish bias.

    Source: nse_fii_dii_participant — fii_fut_idx_long - fii_fut_idx_short
    Normalization: raw net, 5-day delta, 20-day z-score, 60-day rolling percentile
    """
    if fii_dii.empty:
        return {"fii_net_idx_fut": None, "fii_net_idx_fut_delta5": None,
                "fii_net_idx_fut_z20": None, "fii_net_idx_fut_p60": None}

    net = fii_dii["fii_fut_idx_long"] - fii_dii["fii_fut_idx_short"]
    net = net.astype(float)
    current = net.iloc[-1]

    delta5 = None
    if len(net) >= 6:
        delta5 = _safe_float(current - net.iloc[-6])

    z20 = _safe_float(_zscore(net, 20)) if len(net) >= 20 else None
    p60 = _safe_float(_rolling_percentile(net, 60)) if len(net) >= 60 else None

    return {
        "fii_net_idx_fut": _safe_float(current),
        "fii_net_idx_fut_delta5": delta5,
        "fii_net_idx_fut_z20": z20,
        "fii_net_idx_fut_p60": p60,
    }


def compute_fii_net_stk_fut(fii_dii: pd.DataFrame) -> dict:
    """Tier 1A: fii_net_stk_fut

    Hypothesis: FII net stock futures reflects stock-level institutional conviction beyond
    index hedging. Net long stock futures alongside index futures = broad-based bullishness.

    Source: nse_fii_dii_participant — fii_fut_stk_long - fii_fut_stk_short
    Normalization: raw net, 5-day delta, 20-day z-score
    """
    if fii_dii.empty:
        return {"fii_net_stk_fut": None, "fii_net_stk_fut_delta5": None,
                "fii_net_stk_fut_z20": None}

    net = fii_dii["fii_fut_stk_long"] - fii_dii["fii_fut_stk_short"]
    net = net.astype(float)
    current = net.iloc[-1]

    delta5 = _safe_float(current - net.iloc[-6]) if len(net) >= 6 else None
    z20 = _safe_float(_zscore(net, 20)) if len(net) >= 20 else None

    return {
        "fii_net_stk_fut": _safe_float(current),
        "fii_net_stk_fut_delta5": delta5,
        "fii_net_stk_fut_z20": z20,
    }


def compute_fii_options_skew(fii_dii: pd.DataFrame) -> dict:
    """Tier 1A: fii_options_skew

    Hypothesis: FII options call/put positioning reveals hedging vs directional intent.
    Call-heavy = directionally bullish; put-heavy = hedging/bearish.

    Source: nse_fii_dii_participant —
      skew = (call_long + call_short) - (put_long + put_short)
      ratio = call_total / (call_total + put_total)
    Normalization: raw skew, 20-day z-score, ratio form
    """
    if fii_dii.empty:
        return {"fii_options_skew": None, "fii_options_skew_z20": None,
                "fii_options_skew_ratio": None}

    call_total = (fii_dii["fii_opt_idx_call_long"] + fii_dii["fii_opt_idx_call_short"]).astype(float)
    put_total = (fii_dii["fii_opt_idx_put_long"] + fii_dii["fii_opt_idx_put_short"]).astype(float)

    skew = call_total - put_total
    current_skew = skew.iloc[-1]

    z20 = _safe_float(_zscore(skew, 20)) if len(skew) >= 20 else None

    # Ratio form
    denom = call_total.iloc[-1] + put_total.iloc[-1]
    ratio = call_total.iloc[-1] / denom if denom > 0 else None

    return {
        "fii_options_skew": _safe_float(current_skew),
        "fii_options_skew_z20": z20,
        "fii_options_skew_ratio": _safe_float(ratio),
    }


def compute_fii_dii_divergence(fii_dii: pd.DataFrame) -> dict:
    """Tier 1A: fii_dii_divergence

    Hypothesis: When FII and DII take opposite sides, it creates a tug-of-war that
    often resolves in FII's direction. Divergence magnitude predicts resolution intensity.

    Source: (fii_total_long - fii_total_short) - (dii_total_long - dii_total_short)
    Normalization: raw divergence, 20-day z-score
    """
    if fii_dii.empty:
        return {"fii_dii_divergence": None, "fii_dii_divergence_z20": None}

    fii_net = (fii_dii["fii_total_long"] - fii_dii["fii_total_short"]).astype(float)
    dii_net = (fii_dii["dii_total_long"] - fii_dii["dii_total_short"]).astype(float)
    divergence = fii_net - dii_net

    current = divergence.iloc[-1]
    z20 = _safe_float(_zscore(divergence, 20)) if len(divergence) >= 20 else None

    return {
        "fii_dii_divergence": _safe_float(current),
        "fii_dii_divergence_z20": z20,
    }


def compute_client_vs_fii_divergence(fii_dii: pd.DataFrame) -> dict:
    """Tier 1A: client_vs_fii_divergence

    Hypothesis: Retail (client) positioning is historically wrong at extremes. When
    clients are maximally net long and FII net short (or vice versa), reversal is imminent.

    Source: (client_total_long - client_total_short) - (fii_total_long - fii_total_short)
    Normalization: 60-day z-score (extremes are what matter)
    """
    if fii_dii.empty:
        return {"client_vs_fii_divergence": None, "client_vs_fii_divergence_z60": None}

    client_net = (fii_dii["client_total_long"] - fii_dii["client_total_short"]).astype(float)
    fii_net = (fii_dii["fii_total_long"] - fii_dii["fii_total_short"]).astype(float)
    divergence = client_net - fii_net

    current = divergence.iloc[-1]
    z60 = _safe_float(_zscore(divergence, 60)) if len(divergence) >= 60 else None

    return {
        "client_vs_fii_divergence": _safe_float(current),
        "client_vs_fii_divergence_z60": z60,
    }


def compute_fii_acceleration(fii_dii: pd.DataFrame) -> dict:
    """Tier 1A: fii_acceleration (1d / 3d / 5d)

    Hypothesis: Rate of change in FII positioning matters more than level. Accelerating
    net buying = increasing conviction; decelerating = topping.

    Source: delta of fii_net_idx_fut over 1d, 3d, 5d windows
    Normalization: raw delta, 20-day z-score of each window
    """
    if fii_dii.empty or len(fii_dii) < 2:
        return {
            "fii_acceleration_1d": None, "fii_acceleration_3d": None,
            "fii_acceleration_5d": None,
            "fii_acceleration_1d_z20": None, "fii_acceleration_3d_z20": None,
            "fii_acceleration_5d_z20": None,
        }

    net = (fii_dii["fii_fut_idx_long"] - fii_dii["fii_fut_idx_short"]).astype(float)

    result = {}
    for window in [1, 3, 5]:
        key = f"fii_acceleration_{window}d"
        key_z = f"fii_acceleration_{window}d_z20"

        if len(net) > window:
            delta = net.diff(window)
            current_delta = delta.iloc[-1]
            result[key] = _safe_float(current_delta)

            delta_clean = delta.dropna()
            if len(delta_clean) >= 20:
                result[key_z] = _safe_float(_zscore(delta_clean, 20))
            else:
                result[key_z] = None
        else:
            result[key] = None
            result[key_z] = None

    return result


# ---------------------------------------------------------------------------
# Tier 1B: FO Positioning Structure (5 features)
# ---------------------------------------------------------------------------

def _select_near_month_expiry(expiries: list, target_date: date, roll_trading_days: int = 5) -> date | None:
    """Select near-month expiry, rolling to next month if within roll_trading_days trading days."""
    monthly = _get_monthly_expiries(expiries)
    monthly = sorted([e for e in monthly if e >= target_date])
    if not monthly:
        return None
    nearest = monthly[0]
    trading_days_to = _trading_days_until(target_date, nearest)
    if trading_days_to < roll_trading_days and len(monthly) > 1:
        return monthly[1]
    return nearest


def compute_pcr_oi_near_month(fo: pd.DataFrame, target_date: date) -> dict:
    """Tier 1B: pcr_oi_near_month

    Hypothesis: Near-month put-call ratio by OI is the market's aggregate bet on
    direction. Extreme PCR values often mark reversal zones.

    Source: nse_fo_bhavcopy — SUM(oi) WHERE option_type='PE' / SUM(oi) WHERE option_type='CE'
           for NIFTY OPTIDX near-month monthly expiry
    Normalization: raw ratio, 20-day z-score, 60-day rolling percentile
    """
    if fo.empty:
        return {"pcr_oi_near_month": None}

    options = fo[fo["instrument_type"].isin(["IDO", "OPTIDX"])]
    if options.empty:
        return {"pcr_oi_near_month": None}

    expiries = sorted(options["expiry"].dropna().unique())
    if not expiries:
        return {"pcr_oi_near_month": None}

    selected = _select_near_month_expiry(expiries, target_date, roll_trading_days=5)
    if selected is None:
        return {"pcr_oi_near_month": None}

    exp_opts = options[options["expiry"] == selected]
    puts = exp_opts[exp_opts["option_type"] == "PE"]["oi"].sum()
    calls = exp_opts[exp_opts["option_type"] == "CE"]["oi"].sum()

    if calls == 0:
        return {"pcr_oi_near_month": None}

    return {"pcr_oi_near_month": _safe_float(puts / calls)}


def compute_pcr_oi_next_month(fo: pd.DataFrame, target_date: date) -> dict:
    """Tier 1B: pcr_oi_next_month

    Hypothesis: Next-month PCR is more stable and reflects medium-term institutional
    positioning (less noise from weekly expiry traders).

    Source: same as near_month but expiry = <next_month_expiry>
    Normalization: raw ratio, 20-day z-score
    """
    if fo.empty:
        return {"pcr_oi_next_month": None}

    options = fo[fo["instrument_type"].isin(["IDO", "OPTIDX"])]
    if options.empty:
        return {"pcr_oi_next_month": None}

    expiries = sorted(options["expiry"].dropna().unique())
    monthly = _get_monthly_expiries(expiries)
    monthly = sorted([e for e in monthly if e >= target_date])

    if len(monthly) < 2:
        return {"pcr_oi_next_month": None}

    next_month = monthly[1]
    exp_opts = options[options["expiry"] == next_month]
    puts = exp_opts[exp_opts["option_type"] == "PE"]["oi"].sum()
    calls = exp_opts[exp_opts["option_type"] == "CE"]["oi"].sum()

    if calls == 0:
        return {"pcr_oi_next_month": None}

    return {"pcr_oi_next_month": _safe_float(puts / calls)}


def compute_max_pain_distance_pct(fo: pd.DataFrame, target_date: date, nifty_close: float) -> dict:
    """Tier 1B: max_pain_distance_pct

    Hypothesis: Max pain (strike where options sellers lose least) acts as a magnet —
    Nifty tends to drift toward max pain by expiry.

    Source: nse_fo_bhavcopy — compute max pain from all NIFTY OPTIDX strikes for
    near-month expiry. Max pain = strike that minimizes total exercised value.
    Normalization: pct distance from current Nifty close to max pain (signed).
    """
    if fo.empty or nifty_close is None or nifty_close == 0:
        return {"max_pain_distance_pct": None}

    options = fo[fo["instrument_type"].isin(["IDO", "OPTIDX"])]
    if options.empty:
        return {"max_pain_distance_pct": None}

    expiries = sorted(options["expiry"].dropna().unique())
    selected = _select_near_month_expiry(expiries, target_date, roll_trading_days=5)
    if selected is None:
        return {"max_pain_distance_pct": None}

    exp_opts = options[options["expiry"] == selected]
    strikes = sorted(exp_opts["strike"].dropna().unique())

    if len(strikes) < 50:
        return {"max_pain_distance_pct": None}

    # Compute max pain: for each possible settlement price (each strike),
    # calculate total loss to option sellers
    calls = exp_opts[exp_opts["option_type"] == "CE"][["strike", "oi"]].copy()
    puts = exp_opts[exp_opts["option_type"] == "PE"][["strike", "oi"]].copy()

    min_pain = float("inf")
    max_pain_strike = None

    for test_price in strikes:
        # Call sellers lose when test_price > strike
        call_loss = calls.apply(
            lambda r: max(0, test_price - r["strike"]) * r["oi"], axis=1
        ).sum()
        # Put sellers lose when test_price < strike
        put_loss = puts.apply(
            lambda r: max(0, r["strike"] - test_price) * r["oi"], axis=1
        ).sum()
        total_pain = call_loss + put_loss
        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = test_price

    if max_pain_strike is None:
        return {"max_pain_distance_pct": None}

    distance_pct = (nifty_close - max_pain_strike) / max_pain_strike * 100

    return {"max_pain_distance_pct": _safe_float(distance_pct)}


def compute_oi_concentration_atm(fo: pd.DataFrame, target_date: date, nifty_close: float) -> dict:
    """Tier 1B: oi_concentration_atm

    Hypothesis: Heavy OI at ATM strikes = strong support/resistance wall.
    Breakout through high-OI strike = high-conviction move.

    Source: nse_fo_bhavcopy — OI at strikes within +/-2% of Nifty close, near-month OPTIDX
    Normalization: ATM OI as % of total expiry OI, 20-day rolling percentile
    """
    if fo.empty or nifty_close is None or nifty_close == 0:
        return {"oi_concentration_atm": None}

    options = fo[fo["instrument_type"].isin(["IDO", "OPTIDX"])]
    if options.empty:
        return {"oi_concentration_atm": None}

    expiries = sorted(options["expiry"].dropna().unique())
    selected = _select_near_month_expiry(expiries, target_date, roll_trading_days=5)
    if selected is None:
        return {"oi_concentration_atm": None}

    exp_opts = options[options["expiry"] == selected]
    total_oi = exp_opts["oi"].sum()
    if total_oi == 0:
        return {"oi_concentration_atm": None}

    # ATM = strikes within ±2% of Nifty close
    lower = nifty_close * 0.98
    upper = nifty_close * 1.02
    atm_opts = exp_opts[(exp_opts["strike"] >= lower) & (exp_opts["strike"] <= upper)]
    atm_oi = atm_opts["oi"].sum()

    concentration = (atm_oi / total_oi) * 100

    return {"oi_concentration_atm": _safe_float(concentration)}


def compute_buildup_classification(fo_range: pd.DataFrame, target_date: date) -> dict:
    """Tier 1B: buildup_classification

    Hypothesis: OI + price direction combination reveals institutional intent:
      Price up + OI up = long_buildup (bullish)
      Price down + OI up = short_buildup (bearish)
      Price up + OI down = short_covering (weakly bullish)
      Price down + OI down = long_unwinding (weakly bearish)
      OI unchanged = neutral

    Source: nse_fo_bhavcopy — near-month NIFTY FUTIDX
    Normalization: categorical (5 classes) + buildup_intensity (z-score of |oi_change| * |price_change_pct|)
    """
    if fo_range.empty:
        return {"buildup_class": None, "buildup_intensity": None}

    # Get today's data
    today = fo_range[fo_range["date"] == target_date]
    if today.empty:
        return {"buildup_class": None, "buildup_intensity": None}

    # Select near-month futures
    expiries = sorted(today["expiry"].dropna().unique())
    if not expiries:
        return {"buildup_class": None, "buildup_intensity": None}

    # Pick nearest expiry (already filtered to FUTIDX), roll at T-5 trading days
    nearest = expiries[0]
    trading_days_to = _trading_days_until(target_date, nearest)
    if trading_days_to < 5 and len(expiries) > 1:
        nearest = expiries[1]

    today_row = today[today["expiry"] == nearest]
    if today_row.empty:
        return {"buildup_class": None, "buildup_intensity": None}

    row = today_row.iloc[0]
    close_val = row.get("close")
    prev_close = row.get("prev_close")
    oi_change = row.get("oi_change")

    if any(v is None or pd.isna(v) for v in [close_val, prev_close, oi_change]):
        return {"buildup_class": None, "buildup_intensity": None}

    price_up = float(close_val) > float(prev_close)
    price_down = float(close_val) < float(prev_close)
    oi_up = float(oi_change) > 0
    oi_down = float(oi_change) < 0
    oi_zero = float(oi_change) == 0

    if oi_zero:
        buildup_class = "neutral"
    elif price_up and oi_up:
        buildup_class = "long_buildup"
    elif price_down and oi_up:
        buildup_class = "short_buildup"
    elif price_up and oi_down:
        buildup_class = "short_covering"
    elif price_down and oi_down:
        buildup_class = "long_unwinding"
    else:
        buildup_class = "neutral"

    # Intensity = |oi_change| * |price_change_pct|
    price_change_pct = abs(float(close_val) - float(prev_close)) / float(prev_close) * 100 if prev_close != 0 else 0
    intensity = abs(float(oi_change)) * price_change_pct

    return {
        "buildup_class": buildup_class,
        "buildup_intensity": _safe_float(intensity),
    }


# ---------------------------------------------------------------------------
# Tier 1C: Global Shock Structure (1 feature)
# ---------------------------------------------------------------------------

def compute_vix_overnight_gap(vix_df: pd.DataFrame) -> dict:
    """Tier 1C: vix_overnight_gap

    Hypothesis: India VIX gap (today's open vs yesterday's close) captures overnight
    fear/complacency shift. Large VIX gaps should precede regime transitions
    (volatility dimension).

    Source: nse_indices_daily WHERE index = 'India VIX' — gap from consecutive days
    Normalization: pct change, 20-day z-score
    """
    if vix_df.empty or len(vix_df) < 2:
        return {"vix_overnight_gap_pct": None, "vix_overnight_gap_pct_z20": None}

    today = vix_df.iloc[-1]
    yesterday = vix_df.iloc[-2]

    vix_open = today.get("vix_open")
    vix_prev_close = yesterday.get("vix_close")

    if any(v is None or pd.isna(v) for v in [vix_open, vix_prev_close]) or vix_prev_close == 0:
        return {"vix_overnight_gap_pct": None, "vix_overnight_gap_pct_z20": None}

    gap_pct = (float(vix_open) - float(vix_prev_close)) / float(vix_prev_close) * 100

    # 20-day z-score: compute gap series from consecutive days, z-score excludes current
    z20 = None
    if len(vix_df) >= 22:
        vix_opens = vix_df["vix_open"].astype(float)
        vix_closes = vix_df["vix_close"].astype(float)
        # Gap = today's open vs yesterday's close
        gap_series = (vix_opens.iloc[1:].values - vix_closes.iloc[:-1].values) / vix_closes.iloc[:-1].values * 100
        gap_series = pd.Series(gap_series).dropna()
        if len(gap_series) >= 21:
            prior = gap_series.iloc[-21:-1]
            current = gap_series.iloc[-1]
            mean = prior.mean()
            std = prior.std()
            if std > 0 and not np.isnan(std):
                z20 = _safe_float((current - mean) / std)

    return {
        "vix_overnight_gap_pct": _safe_float(gap_pct),
        "vix_overnight_gap_pct_z20": z20,
    }


# ---------------------------------------------------------------------------
# Tier 2A: CM Breadth Quality (4 features)
# ---------------------------------------------------------------------------

def compute_turnover_weighted_breadth(cm: pd.DataFrame, target_date: date) -> dict:
    """Tier 2A: turnover_weighted_breadth

    Hypothesis: Weighting advances/declines by turnover gives more signal than
    equal-weight A/D ratio — a large-cap advancing matters more than a microcap.

    Source: nse_cm_bhavcopy — sum(traded_value WHERE close > prev_close) / sum(traded_value)
    Normalization: raw ratio (0-1), 5-day SMA, 20-day z-score
    """
    cm_today = cm[cm["date"] == target_date]
    if cm_today.empty:
        return {"turnover_weighted_breadth": None, "turnover_weighted_breadth_z20": None}

    total_turnover = cm_today["traded_value"].sum()
    if total_turnover == 0 or pd.isna(total_turnover):
        return {"turnover_weighted_breadth": None, "turnover_weighted_breadth_z20": None}

    advancing = cm_today[cm_today["close"] > cm_today["prev_close"]]
    adv_turnover = advancing["traded_value"].sum()

    ratio = adv_turnover / total_turnover

    # Z-score from historical
    z20 = None
    dates = sorted(cm["date"].unique())
    dates = [d for d in dates if d <= target_date]
    if len(dates) >= 20:
        ratios = []
        for d in dates[-20:]:
            day = cm[cm["date"] == d]
            tot = day["traded_value"].sum()
            if tot > 0:
                adv = day[day["close"] > day["prev_close"]]["traded_value"].sum()
                ratios.append(adv / tot)
        if len(ratios) >= 20:
            s = pd.Series(ratios)
            z20 = _safe_float(_zscore(s, 20))

    return {
        "turnover_weighted_breadth": _safe_float(ratio),
        "turnover_weighted_breadth_z20": z20,
    }


def compute_leadership_concentration(cm: pd.DataFrame, target_date: date) -> dict:
    """Tier 2A: leadership_concentration

    Hypothesis: When top N stocks by turnover account for unusually high % of total
    market turnover, the rally/selloff is narrow and fragile.

    Source: nse_cm_bhavcopy — top 10 stocks by traded_value / total traded_value
    Normalization: raw % concentration, 60-day rolling percentile
    """
    cm_today = cm[cm["date"] == target_date]
    if cm_today.empty:
        return {"leadership_concentration": None, "leadership_concentration_p60": None}

    total = cm_today["traded_value"].sum()
    if total == 0 or pd.isna(total):
        return {"leadership_concentration": None, "leadership_concentration_p60": None}

    top10 = cm_today.nlargest(10, "traded_value")["traded_value"].sum()
    concentration = (top10 / total) * 100

    # 60-day percentile
    p60 = None
    dates = sorted(cm["date"].unique())
    dates = [d for d in dates if d <= target_date]
    if len(dates) >= 60:
        concs = []
        for d in dates[-60:]:
            day = cm[cm["date"] == d]
            tot = day["traded_value"].sum()
            if tot > 0:
                t10 = day.nlargest(10, "traded_value")["traded_value"].sum()
                concs.append(t10 / tot * 100)
        if len(concs) >= 60:
            s = pd.Series(concs)
            p60 = _safe_float(_rolling_percentile(s, 60))

    return {
        "leadership_concentration": _safe_float(concentration),
        "leadership_concentration_p60": p60,
    }


def compute_cross_sectional_dispersion(cm: pd.DataFrame, target_date: date) -> dict:
    """Tier 2A: cross_sectional_dispersion

    Hypothesis: High return dispersion across stocks = stock-picker's market.
    Low dispersion = index-driven, unfavorable for stock selection.

    Source: nse_cm_bhavcopy — std(daily_return) across all stocks
    Normalization: raw std, 60-day rolling percentile, 20-day z-score
    """
    cm_today = cm[cm["date"] == target_date]
    if cm_today.empty or len(cm_today) < 10:
        return {"cross_sectional_dispersion": None, "cross_sectional_dispersion_z20": None}

    # Daily return for each stock
    valid = cm_today[cm_today["prev_close"] > 0].copy()
    if len(valid) < 10:
        return {"cross_sectional_dispersion": None, "cross_sectional_dispersion_z20": None}

    returns = (valid["close"] - valid["prev_close"]) / valid["prev_close"]
    dispersion = returns.std() * 100  # as percentage

    # Z-score
    z20 = None
    dates = sorted(cm["date"].unique())
    dates = [d for d in dates if d <= target_date]
    if len(dates) >= 20:
        dispersions = []
        for d in dates[-20:]:
            day = cm[cm["date"] == d]
            v = day[day["prev_close"] > 0]
            if len(v) >= 10:
                r = (v["close"] - v["prev_close"]) / v["prev_close"]
                dispersions.append(r.std() * 100)
        if len(dispersions) >= 20:
            s = pd.Series(dispersions)
            z20 = _safe_float(_zscore(s, 20))

    return {
        "cross_sectional_dispersion": _safe_float(dispersion),
        "cross_sectional_dispersion_z20": z20,
    }


def compute_volume_concentration(cm: pd.DataFrame, target_date: date) -> dict:
    """Tier 2A: volume_concentration

    Hypothesis: % of market volume in top N names — complementary to
    leadership_concentration but uses volume instead of turnover.

    Source: nse_cm_bhavcopy — top 20 stocks by volume / total volume
    Normalization: raw %, 60-day rolling percentile
    """
    cm_today = cm[cm["date"] == target_date]
    if cm_today.empty:
        return {"volume_concentration": None, "volume_concentration_p60": None}

    total = cm_today["volume"].sum()
    if total == 0 or pd.isna(total):
        return {"volume_concentration": None, "volume_concentration_p60": None}

    top20 = cm_today.nlargest(20, "volume")["volume"].sum()
    concentration = (top20 / total) * 100

    # 60-day percentile
    p60 = None
    dates = sorted(cm["date"].unique())
    dates = [d for d in dates if d <= target_date]
    if len(dates) >= 60:
        concs = []
        for d in dates[-60:]:
            day = cm[cm["date"] == d]
            tot = day["volume"].sum()
            if tot > 0:
                t20 = day.nlargest(20, "volume")["volume"].sum()
                concs.append(t20 / tot * 100)
        if len(concs) >= 60:
            s = pd.Series(concs)
            p60 = _safe_float(_rolling_percentile(s, 60))

    return {
        "volume_concentration": _safe_float(concentration),
        "volume_concentration_p60": p60,
    }


# ---------------------------------------------------------------------------
# Tier 2B: Volatility Conditioning Refinements (2 features)
# ---------------------------------------------------------------------------

def compute_implied_vs_realized_vol_spread(
    vix_df: pd.DataFrame, nifty_df: pd.DataFrame
) -> dict:
    """Tier 2B: implied_vs_realized_vol_spread

    Hypothesis: When implied vol (VIX) >> realized vol, market is over-hedged and
    likely to mean-revert. When IV << RV, complacency is dangerous.

    Source: nse_indices_daily (India VIX annualized) vs 20-day realized vol from
    Nifty daily returns
    Normalization: raw spread (IV - RV), 60-day z-score, ratio (IV/RV)
    """
    if vix_df.empty or nifty_df.empty or len(nifty_df) < 21:
        return {"iv_rv_spread": None, "iv_rv_ratio": None, "iv_rv_spread_z60": None}

    # VIX is already annualized
    iv = float(vix_df.iloc[-1]["vix_close"])

    # Compute 20-day realized vol (annualized)
    nifty_close = nifty_df["close"].astype(float)
    log_returns = np.log(nifty_close / nifty_close.shift(1)).dropna()
    if len(log_returns) < 20:
        return {"iv_rv_spread": None, "iv_rv_ratio": None, "iv_rv_spread_z60": None}

    rv = log_returns.iloc[-20:].std() * np.sqrt(252) * 100  # annualized, in %

    spread = iv - rv
    ratio = iv / rv if rv > 0 else None

    # 60-day z-score of IV-RV spread: compute spread for each of the last 60+ days,
    # then z-score the current day's spread against that history.
    z60 = None
    if len(log_returns) >= 80 and len(vix_df) >= 61:
        vix_closes = vix_df["vix_close"].astype(float)
        # Build a spread series: for each day with VIX data, compute IV - 20d RV
        spreads = []
        # We need at least 20 returns before each VIX day to compute RV
        vix_dates = vix_df["date"].tolist()
        nifty_dates = nifty_df["date"].tolist()
        nifty_close_vals = nifty_df["close"].astype(float)

        for i in range(len(vix_df)):
            vix_val = float(vix_closes.iloc[i])
            vix_date = vix_dates[i]
            # Find Nifty returns up to this date
            nifty_mask = [d <= vix_date for d in nifty_dates]
            nifty_subset = nifty_close_vals[nifty_mask]
            if len(nifty_subset) < 21:
                spreads.append(np.nan)
                continue
            lr = np.log(nifty_subset / nifty_subset.shift(1)).dropna()
            if len(lr) < 20:
                spreads.append(np.nan)
                continue
            rv_i = lr.iloc[-20:].std() * np.sqrt(252) * 100
            spreads.append(vix_val - rv_i)

        spread_series = pd.Series(spreads).dropna()
        if len(spread_series) >= 61:
            # Z-score: current spread vs prior 60 (exclude current from window)
            prior = spread_series.iloc[-61:-1]
            current_spread = spread_series.iloc[-1]
            s_mean = prior.mean()
            s_std = prior.std()
            if s_std > 0 and not np.isnan(s_std):
                z60 = _safe_float((current_spread - s_mean) / s_std)

    return {
        "iv_rv_spread": _safe_float(spread),
        "iv_rv_ratio": _safe_float(ratio),
        "iv_rv_spread_z60": z60,
    }


def compute_overnight_vol_vs_session_vol(
    nseix_vol: pd.DataFrame, nifty_df: pd.DataFrame
) -> dict:
    """Tier 2B: overnight_vol_vs_session_vol

    Hypothesis: Ratio of overnight (GIFT) volatility to daytime (NSE) volatility
    reveals whether risk is being resolved globally or domestically.

    Source: nseix_overnight_vol (Nifty annualized vol) vs nse_indices_daily-derived session vol
    Normalization: ratio, 20-day z-score of ratio
    """
    if nseix_vol.empty or nifty_df.empty or len(nifty_df) < 21:
        return {"overnight_vs_session_vol": None, "overnight_vs_session_vol_z20": None}

    overnight_vol = nseix_vol.iloc[-1].get("applicable_ann_vol")
    if overnight_vol is None or pd.isna(overnight_vol):
        return {"overnight_vs_session_vol": None, "overnight_vs_session_vol_z20": None}

    # Session vol: 20-day realized vol from NSE intraday (close-to-close)
    nifty_close = nifty_df["close"].astype(float)
    log_returns = np.log(nifty_close / nifty_close.shift(1)).dropna()
    if len(log_returns) < 20:
        return {"overnight_vs_session_vol": None, "overnight_vs_session_vol_z20": None}

    session_vol = log_returns.iloc[-20:].std() * np.sqrt(252) * 100

    if session_vol == 0 or np.isnan(session_vol):
        return {"overnight_vs_session_vol": None, "overnight_vs_session_vol_z20": None}

    ratio = float(overnight_vol) / session_vol

    # Z-score of ratio over history
    z20 = None
    if len(nseix_vol) >= 20 and len(log_returns) >= 40:
        ratios = []
        ann_vols = nseix_vol["applicable_ann_vol"].astype(float).dropna()
        # Simple approach: compute ratio for each available date
        for i in range(max(0, len(ann_vols) - 20), len(ann_vols)):
            ov = ann_vols.iloc[i]
            # Use a rolling window for session vol
            end_idx = min(i + 20, len(log_returns))
            start_idx = max(0, end_idx - 20)
            if end_idx - start_idx >= 10:
                sv = log_returns.iloc[start_idx:end_idx].std() * np.sqrt(252) * 100
                if sv > 0:
                    ratios.append(ov / sv)
        if len(ratios) >= 20:
            s = pd.Series(ratios)
            z20 = _safe_float(_zscore(s, 20))

    return {
        "overnight_vs_session_vol": _safe_float(ratio),
        "overnight_vs_session_vol_z20": z20,
    }


# ---------------------------------------------------------------------------
# Tier 2C: Index Divergence (4 features)
# ---------------------------------------------------------------------------

def _fetch_indices_multi(end_date: date, lookback_days: int = 15) -> pd.DataFrame:
    """Fetch multiple index close prices for divergence features."""
    start_date = end_date - timedelta(days=lookback_days + 10)
    return _read_sql(
        """
        SELECT date, index, close FROM nse_indices_daily
        WHERE index IN ('Nifty 50', 'Nifty 500', 'Nifty Midcap 150', 'Nifty Smallcap 250')
          AND date >= %s AND date <= %s
        ORDER BY date ASC
        """,
        params=[start_date, end_date],
    )


def compute_index_divergence(indices_df: pd.DataFrame, target_date: date) -> dict:
    """Tier 2C: Index Divergence — 4 features.

    Measures relative performance of broader/smaller indices vs Nifty 50 over 5 trading days.
    Reveals whether rallies are broad-based or narrow.
    """
    result = {
        "nifty500_vs_nifty50_return_5d": None,
        "midcap150_vs_nifty50_return_5d": None,
        "smallcap250_vs_nifty50_return_5d": None,
        "index_breadth_divergence": None,
    }

    if indices_df.empty:
        return result

    def _get_return_5d(idx_name: str) -> float | None:
        sub = indices_df[indices_df["index"] == idx_name].sort_values("date")
        sub = sub[sub["date"] <= target_date]
        if len(sub) < 6:
            return None
        close_now = float(sub.iloc[-1]["close"])
        close_5d = float(sub.iloc[-6]["close"])
        if close_5d == 0:
            return None
        return (close_now - close_5d) / close_5d

    n50_ret = _get_return_5d("Nifty 50")
    n500_ret = _get_return_5d("Nifty 500")
    mid150_ret = _get_return_5d("Nifty Midcap 150")
    sc250_ret = _get_return_5d("Nifty Smallcap 250")

    if n50_ret is not None and n500_ret is not None:
        result["nifty500_vs_nifty50_return_5d"] = _safe_float(n500_ret - n50_ret)
    if n50_ret is not None and mid150_ret is not None:
        result["midcap150_vs_nifty50_return_5d"] = _safe_float(mid150_ret - n50_ret)
    if n50_ret is not None and sc250_ret is not None:
        result["smallcap250_vs_nifty50_return_5d"] = _safe_float(sc250_ret - n50_ret)

    # index_breadth_divergence: conditional signal
    if n50_ret is not None and n500_ret is not None:
        if n50_ret > 0:
            # Nifty 50 is up — check if broad market is also up
            result["index_breadth_divergence"] = _safe_float(
                1.0 if n500_ret > n50_ret else -1.0
            )
        else:
            # Both down
            result["index_breadth_divergence"] = _safe_float(0.0)

    return result


# ---------------------------------------------------------------------------
# Main entry point — compute all 28 features for a given date
# ---------------------------------------------------------------------------

def compute_v2_features(target_date: date) -> dict:
    """Compute all v2 features for target_date.

    Returns a flat dict with all feature values. Missing features are None.
    Gracefully degrades: Tier 0 requires NSEIX data (Jul 2023+), other tiers
    work from Jan 2020+.
    """
    features = {}
    missing_families = []

    # --- Fetch data ---

    # Tier 0: NSEIX data (90-day lookback for percentile features)
    nseix_fo = _fetch_nseix_fo_nifty(target_date, lookback_days=90)
    nseix_vol = _fetch_nseix_vol_nifty(target_date, lookback_days=90)

    # Nifty close for gap calculation
    nifty_pair = _fetch_nifty_returns(target_date, lookback_days=5)
    nifty_prev_close = None
    nifty_close = None
    if len(nifty_pair) >= 2:
        nifty_prev_close = float(nifty_pair.iloc[-2]["close"])
        nifty_close = float(nifty_pair.iloc[-1]["close"])
    elif len(nifty_pair) == 1:
        nifty_close = float(nifty_pair.iloc[-1]["close"])

    # Tier 1A: FII/DII
    fii_dii = _fetch_fii_dii_full(target_date, lookback_days=65)

    # Tier 1B: FO bhavcopy
    fo = _fetch_fo_bhavcopy_nifty(target_date)
    fo_range = _fetch_fo_bhavcopy_nifty_range(target_date, lookback_days=5)

    # Tier 2A: CM bhavcopy (need 60+ days for percentiles)
    cm = _fetch_cm_bhavcopy_for_breadth(target_date, lookback_days=70)

    # Tier 1C + 2B: VIX + Nifty returns (need 80+ days for z-scores, 25+ for VIX gap z20)
    nifty_returns = _fetch_nifty_returns(target_date, lookback_days=85)
    vix = _fetch_india_vix(target_date, lookback_days=65)

    # --- Compute features ---

    # Tier 0: Overnight State (5 features)
    if not nseix_fo.empty:
        features.update(compute_overnight_gap_pct(nseix_fo, nifty_prev_close))
        features.update(compute_overnight_range_pct(nseix_fo))
        features.update(compute_overnight_oi_change_pct(nseix_fo))
        features.update(compute_overnight_volume_conviction(nseix_fo))
    else:
        missing_families.append("tier0_overnight")
        for k in ["overnight_gap_pct", "overnight_gap_pct_z20",
                   "overnight_range_pct", "overnight_range_pct_p20", "overnight_range_pct_p60",
                   "overnight_oi_change_pct", "overnight_oi_change_pct_z20",
                   "overnight_volume_conviction"]:
            features[k] = None

    if not nseix_vol.empty:
        features.update(compute_overnight_vol_delta(nseix_vol))
    else:
        if "tier0_overnight" not in missing_families:
            missing_families.append("tier0_overnight")
        features["overnight_vol_delta"] = None
        features["overnight_vol_delta_z20"] = None

    # Tier 1A: FII/DII Institutional (6 features)
    if not fii_dii.empty:
        features.update(compute_fii_net_idx_fut(fii_dii))
        features.update(compute_fii_net_stk_fut(fii_dii))
        features.update(compute_fii_options_skew(fii_dii))
        features.update(compute_fii_dii_divergence(fii_dii))
        features.update(compute_client_vs_fii_divergence(fii_dii))
        features.update(compute_fii_acceleration(fii_dii))
    else:
        missing_families.append("tier1a_fii_dii")
        for k in ["fii_net_idx_fut", "fii_net_idx_fut_delta5", "fii_net_idx_fut_z20", "fii_net_idx_fut_p60",
                   "fii_net_stk_fut", "fii_net_stk_fut_delta5", "fii_net_stk_fut_z20",
                   "fii_options_skew", "fii_options_skew_z20", "fii_options_skew_ratio",
                   "fii_dii_divergence", "fii_dii_divergence_z20",
                   "client_vs_fii_divergence", "client_vs_fii_divergence_z60",
                   "fii_acceleration_1d", "fii_acceleration_3d", "fii_acceleration_5d",
                   "fii_acceleration_1d_z20", "fii_acceleration_3d_z20", "fii_acceleration_5d_z20"]:
            features[k] = None

    # Tier 1B: FO Positioning (5 features)
    if not fo.empty:
        features.update(compute_pcr_oi_near_month(fo, target_date))
        features.update(compute_pcr_oi_next_month(fo, target_date))
        features.update(compute_max_pain_distance_pct(fo, target_date, nifty_close))
        features.update(compute_oi_concentration_atm(fo, target_date, nifty_close))
    else:
        missing_families.append("tier1b_fo_positioning")
        for k in ["pcr_oi_near_month", "pcr_oi_next_month",
                   "max_pain_distance_pct", "oi_concentration_atm"]:
            features[k] = None

    if not fo_range.empty:
        features.update(compute_buildup_classification(fo_range, target_date))
    else:
        features["buildup_class"] = None
        features["buildup_intensity"] = None

    # Tier 1C: Global Shock Structure (1 feature)
    if not vix.empty and len(vix) >= 2:
        features.update(compute_vix_overnight_gap(vix))
    else:
        missing_families.append("tier1c_global_shock")
        features["vix_overnight_gap_pct"] = None
        features["vix_overnight_gap_pct_z20"] = None

    # Tier 2A: CM Breadth Quality (4 features)
    if not cm.empty:
        features.update(compute_turnover_weighted_breadth(cm, target_date))
        features.update(compute_leadership_concentration(cm, target_date))
        features.update(compute_cross_sectional_dispersion(cm, target_date))
        features.update(compute_volume_concentration(cm, target_date))
    else:
        missing_families.append("tier2a_breadth")
        for k in ["turnover_weighted_breadth", "turnover_weighted_breadth_z20",
                   "leadership_concentration", "leadership_concentration_p60",
                   "cross_sectional_dispersion", "cross_sectional_dispersion_z20",
                   "volume_concentration", "volume_concentration_p60"]:
            features[k] = None

    # Tier 2B: Volatility Conditioning (2 features)
    features.update(compute_implied_vs_realized_vol_spread(vix, nifty_returns))
    features.update(compute_overnight_vol_vs_session_vol(nseix_vol, nifty_returns))

    # Tier 2C: Index Divergence (4 features)
    indices_multi = _fetch_indices_multi(target_date, lookback_days=15)
    if not indices_multi.empty:
        features.update(compute_index_divergence(indices_multi, target_date))
    else:
        missing_families.append("tier2c_index_divergence")
        for k in ["nifty500_vs_nifty50_return_5d", "midcap150_vs_nifty50_return_5d",
                   "smallcap250_vs_nifty50_return_5d", "index_breadth_divergence"]:
            features[k] = None

    # Meta
    features["_missing_v2_families"] = missing_families

    logger.info(
        "v2 features for %s: %d computed, %d None, missing_families=%s",
        target_date,
        sum(1 for v in features.values() if v is not None and not isinstance(v, list)),
        sum(1 for k, v in features.items() if v is None and not k.startswith("_")),
        missing_families,
    )

    return features
