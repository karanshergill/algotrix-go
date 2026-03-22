"""Compute all regime features from raw tables for a given date.

Anti-leakage contract: for date D, only data from dates <= D is used.
Phase 2: 5-dimension scoring with graceful degradation for missing data.
"""

import json
import logging
from datetime import date

import numpy as np
import pandas as pd

from src import indicators
from src.config import (
    AD_RATIO_AVG_WINDOW,
    ATR_PERIOD,
    EMA_PERIOD,
    HURST_WINDOW,
    MIN_HISTORY_DAYS,
    PERCENTILE_WINDOW,
)
from src.db import (
    check_data_available,
    fetch_cm_bhavcopy,
    fetch_fii_dii,
    fetch_fo_bhavcopy_with_expiry,
    fetch_global_cues,
    fetch_india_vix,
    fetch_nifty_ohlcv,
    fetch_nseix_settlement,
)

logger = logging.getLogger(__name__)

_NIFTY50_ISINS: list[str] | None = None


def _load_nifty50_isins() -> list[str]:
    global _NIFTY50_ISINS
    if _NIFTY50_ISINS is not None:
        return _NIFTY50_ISINS
    import os
    data_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "nifty50_constituents.json")
    with open(data_path) as f:
        data = json.load(f)
    _NIFTY50_ISINS = data["isins"]
    return _NIFTY50_ISINS


class DataNotAvailableError(Exception):
    pass


def compute_features(target_date: date) -> dict:
    """Compute all Phase 2 regime features for target_date.

    Returns dict with all indicator values + metadata.
    Raises DataNotAvailableError if CORE feeds (nifty, vix, cm, fo) are missing.
    Gracefully handles missing optional feeds (fii_dii, nseix, global_cues).
    """
    availability = check_data_available(target_date)

    # Core feeds — hard requirement
    core_missing = [k for k in ["nifty_index", "india_vix", "cm_bhavcopy", "fo_bhavcopy"]
                    if not availability.get(k)]
    if core_missing:
        raise DataNotAvailableError(
            f"Missing core data for {target_date}: {', '.join(core_missing)}"
        )

    # Track missing indicators for schema versioning
    missing_indicators = []

    # ----- Fetch raw data -----
    lookback = max(PERCENTILE_WINDOW + ATR_PERIOD + 10, HURST_WINDOW + 10, MIN_HISTORY_DAYS, 210)
    nifty = fetch_nifty_ohlcv(target_date, lookback_days=lookback)
    vix = fetch_india_vix(target_date, lookback_days=10)
    cm = fetch_cm_bhavcopy(target_date, lookback_days=max(EMA_PERIOD + 15, 45))
    fo = fetch_fo_bhavcopy_with_expiry(target_date)

    if nifty.empty or len(nifty) < ATR_PERIOD + 1:
        raise DataNotAvailableError(f"Insufficient Nifty history for {target_date}")

    # Optional feeds — graceful degradation
    fii_dii = None
    if availability.get("fii_dii"):
        fii_dii = fetch_fii_dii(target_date, lookback_days=5)
        if fii_dii.empty:
            fii_dii = None

    ix_settlement = None
    if availability.get("nseix"):
        ix_settlement = fetch_nseix_settlement(target_date, lookback_days=3)
        if ix_settlement.empty:
            ix_settlement = None

    global_cues = None
    if availability.get("global_cues"):
        global_cues = fetch_global_cues(target_date, lookback_days=5)
        if global_cues.empty:
            global_cues = None

    # ----- Volatility -----
    nifty_open = nifty["open"].astype(float)
    nifty_high = nifty["high"].astype(float)
    nifty_low = nifty["low"].astype(float)
    nifty_close = nifty["close"].astype(float)

    atr = indicators.compute_atr(nifty_high, nifty_low, nifty_close)
    atr_pctile = indicators.compute_atr_percentile(atr)
    vix_close = float(vix.iloc[-1]["vix_close"]) if not vix.empty else np.nan

    vix_roc_5d = np.nan
    if not vix.empty and len(vix) >= 6:
        vix_roc_5d = indicators.compute_vix_roc_5d(vix["vix_close"])
    if np.isnan(vix_roc_5d):
        missing_indicators.append("vix_roc_5d")

    yang_zhang_vol = indicators.compute_yang_zhang_vol(nifty_open, nifty_high, nifty_low, nifty_close)
    garman_klass_vol = indicators.compute_garman_klass_vol(nifty_open, nifty_high, nifty_low, nifty_close)

    # ----- Trend -----
    adx = indicators.compute_adx(nifty_high, nifty_low, nifty_close)
    ema20 = indicators.compute_ema(nifty_close, period=20)
    ema50 = indicators.compute_ema(nifty_close, period=50)
    ema200 = indicators.compute_ema(nifty_close, period=200)

    ema20_distance = indicators.compute_ema_distance_pct(nifty_close, ema20)
    ema50_distance = indicators.compute_ema_distance_pct(nifty_close, ema50)
    ema200_distance = indicators.compute_ema_distance_pct(nifty_close, ema200)
    ema20_slope = indicators.compute_ema_slope(ema20)
    nifty_return_5d = indicators.compute_nifty_return_5d(nifty_close)

    # Breadth momentum 5d — computed from CM bhavcopy
    breadth_momentum_5d = np.nan
    try:
        breadth_momentum_5d = indicators.compute_breadth_momentum_5d(cm, target_date)
    except Exception as e:
        logger.debug("breadth_momentum_5d failed: %s", e)
    if np.isnan(breadth_momentum_5d) if isinstance(breadth_momentum_5d, float) else breadth_momentum_5d is None:
        missing_indicators.append("breadth_momentum_5d")
        breadth_momentum_5d = np.nan

    # ----- Participation -----
    cm_today = cm[cm["date"] == target_date].copy()
    ad_ratio_val = indicators.compute_ad_ratio(cm_today)

    recent_dates = sorted(cm["date"].unique())
    recent_dates = [d for d in recent_dates if d <= target_date]
    ad_ratios = []
    for d in recent_dates[-AD_RATIO_AVG_WINDOW:]:
        day_data = cm[cm["date"] == d]
        ad_ratios.append(indicators.compute_ad_ratio(day_data))
    ad_ratio_5d = float(np.nanmean(ad_ratios)) if ad_ratios else np.nan

    cm_pivot = cm.pivot_table(index="date", columns="isin", values="close")
    cm_pivot = cm_pivot.sort_index()
    universe_pct = indicators.compute_pct_above_ema20(cm_pivot)

    nifty50_isins = _load_nifty50_isins()
    n50_cols = [c for c in cm_pivot.columns if c in nifty50_isins]
    n50_pct = indicators.compute_pct_above_ema20(cm_pivot[n50_cols]) if n50_cols else np.nan

    volume_trend_ratio = indicators.compute_volume_trend_ratio(cm, target_date)
    up_volume_ratio = indicators.compute_up_volume_ratio(cm_today)

    # ----- Sentiment -----
    nifty_pcr_oi = indicators.compute_pcr_oi_nearest_expiry(fo, target_date)
    nifty_fut_basis_pct = indicators.compute_futures_basis_pct(fo)

    fii_net_idx_fut_oi = np.nan
    fii_vs_client_ratio = np.nan
    if fii_dii is not None and not fii_dii.empty:
        today_row = fii_dii.iloc[-1]
        fii_net_idx_fut_oi = indicators.compute_fii_net_idx_fut_oi(today_row)
        fii_vs_client_ratio = indicators.compute_fii_vs_client_ratio(today_row)
    else:
        missing_indicators.extend(["fii_net_idx_fut_oi", "fii_vs_client_ratio"])

    # ----- Institutional Flow -----
    fii_flow_delta = np.nan
    dii_flow_delta = np.nan
    if fii_dii is not None and len(fii_dii) >= 2:
        fii_flow_delta = indicators.compute_fii_flow_delta(fii_dii)
        dii_flow_delta = indicators.compute_dii_flow_delta(fii_dii)
    else:
        missing_indicators.extend(["fii_flow_delta", "dii_flow_delta"])

    gift_nifty_overnight_gap = np.nan
    if ix_settlement is not None and not ix_settlement.empty:
        ix_sorted = ix_settlement.sort_values("date")
        ix_latest = float(ix_sorted.iloc[-1]["settlement_price"])
        # prev NSE Nifty close = the close of the date BEFORE the IX date
        prev_nifty = float(nifty_close.iloc[-2]) if len(nifty_close) >= 2 else np.nan
        gift_nifty_overnight_gap = indicators.compute_gift_nifty_overnight_gap(ix_latest, prev_nifty)
    else:
        missing_indicators.append("gift_nifty_overnight_gap")

    sp500_overnight_return = np.nan
    dxy_overnight_change = np.nan
    us10y_overnight_change = np.nan
    if global_cues is not None and len(global_cues) >= 2:
        if "sp500" in global_cues.columns:
            sp500_overnight_return = indicators.compute_overnight_return(global_cues["sp500"])
        if "dxy" in global_cues.columns:
            dxy_overnight_change = indicators.compute_overnight_return(global_cues["dxy"])
        if "us10y" in global_cues.columns:
            us10y_overnight_change = indicators.compute_overnight_return(global_cues["us10y"])
    else:
        missing_indicators.extend(["sp500_overnight_return", "dxy_overnight_change", "us10y_overnight_change"])

    # ----- Availability regime classification -----
    has_ix = ix_settlement is not None and not ix_settlement.empty
    has_fii = fii_dii is not None and not fii_dii.empty
    has_global = global_cues is not None and len(global_cues) >= 2

    if has_ix and has_fii and has_global:
        availability_regime = "full"
    elif has_fii and has_global and not has_ix:
        availability_regime = "pre_ix"
    else:
        availability_regime = "partial"

    source_start = nifty["date"].iloc[0]
    source_end = nifty["date"].iloc[-1]

    features = {
        # Volatility
        "india_vix_close": _safe_float(vix_close),
        "vix_roc_5d": _safe_float(vix_roc_5d),
        "nifty_yang_zhang_vol": _safe_float(yang_zhang_vol),
        "nifty_garman_klass_vol": _safe_float(garman_klass_vol),
        "nifty_atr_pctile_60d": _safe_float(atr_pctile.iloc[-1]),
        # Trend
        "nifty_ema20_distance": _safe_float(ema20_distance.iloc[-1]),
        "nifty_ema50_distance": _safe_float(ema50_distance.iloc[-1]),
        "nifty_ema200_distance": _safe_float(ema200_distance.iloc[-1]),
        "nifty_ema20_slope": _safe_float(ema20_slope.iloc[-1]),
        "nifty_adx14": _safe_float(adx.iloc[-1]),
        "nifty_return_5d": _safe_float(nifty_return_5d),
        "breadth_momentum_5d": _safe_float(breadth_momentum_5d),
        # Participation
        "ad_ratio": _safe_float(ad_ratio_val),
        "ad_ratio_5d_avg": _safe_float(ad_ratio_5d),
        "universe_pct_above_ema20": _safe_float(universe_pct),
        "nifty50_pct_above_ema20": _safe_float(n50_pct),
        "volume_trend_ratio": _safe_float(volume_trend_ratio),
        "up_volume_ratio": _safe_float(up_volume_ratio),
        # Sentiment
        "nifty_pcr_oi": _safe_float(nifty_pcr_oi),
        "nifty_fut_basis_pct": _safe_float(nifty_fut_basis_pct),
        "fii_net_idx_fut_oi": _safe_float(fii_net_idx_fut_oi),
        "fii_vs_client_ratio": _safe_float(fii_vs_client_ratio),
        # Institutional Flow
        "fii_flow_delta": _safe_float(fii_flow_delta),
        "dii_flow_delta": _safe_float(dii_flow_delta),
        "gift_nifty_overnight_gap": _safe_float(gift_nifty_overnight_gap),
        "sp500_overnight_return": _safe_float(sp500_overnight_return),
        "dxy_overnight_change": _safe_float(dxy_overnight_change),
        "us10y_overnight_change": _safe_float(us10y_overnight_change),
        # Meta
        "_source_window_start": source_start,
        "_source_window_end": source_end,
        "_availability_regime": availability_regime,
        "_missing_indicators": missing_indicators,
    }

    logger.info(
        "Features for %s: VIX=%.1f ADX=%.1f A/D=%.2f PCR=%.2f regime=%s missing=%d",
        target_date,
        features.get("india_vix_close", 0) or 0,
        features.get("nifty_adx14", 0) or 0,
        features.get("ad_ratio", 0) or 0,
        features.get("nifty_pcr_oi", 0) or 0,
        availability_regime,
        len(missing_indicators),
    )

    return features


def _safe_float(val) -> float | None:
    """Convert to Python float, returning None for NaN/None."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None
