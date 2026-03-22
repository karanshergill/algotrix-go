"""Batch-rebuild feature matrix — all data loaded upfront, zero per-date SQL.

Replaces rebuild_feature_matrix.py which fires ~18 SQL queries per date.
This version loads everything into DataFrames once, then iterates in-memory.
"""

import calendar
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.db import _read_sql
from src.global_data import load_sp500, load_usdinr, get_sp500_overnight_return, get_usdinr_overnight_change
from src.preopen_features import PREOPEN_FEATURE_COLS
from src.v2_features import _safe_float


# ---------------------------------------------------------------------------
# Batch data loader
# ---------------------------------------------------------------------------

def load_all_data() -> dict:
    """Load ALL required data into DataFrames once."""
    data = {}
    t0 = time.time()

    print("  Loading regime_ground_truth...")
    data["ground_truth"] = _read_sql(
        "SELECT date, coincident_label FROM regime_ground_truth ORDER BY date"
    )
    data["ground_truth"]["date"] = pd.to_datetime(data["ground_truth"]["date"]).dt.date

    print("  Loading nse_indices_daily (all needed indices)...")
    indices = [
        "Nifty 50", "India VIX", "Nifty 500", "Nifty Midcap 150",
        "Nifty Smallcap 250", "Nifty Bank", "Nifty FMCG", "Nifty Pharma",
        "Nifty IT", "Nifty Metal", "Nifty Auto", "Nifty Realty",
    ]
    data["indices"] = _read_sql(
        """SELECT index, date, open, high, low, close, volume
           FROM nse_indices_daily
           WHERE index = ANY(%s)
           ORDER BY date""",
        params=[indices],
    )
    data["indices"]["date"] = pd.to_datetime(data["indices"]["date"]).dt.date

    print("  Loading nse_fo_bhavcopy (NIFTY only)...")
    data["fo_bhav"] = _read_sql(
        """SELECT date, instrument_type, option_type, strike, expiry,
                  open, high, low, close, prev_close, oi, oi_change, volume, underlying
           FROM nse_fo_bhavcopy
           WHERE symbol = 'NIFTY'
           ORDER BY date, expiry"""
    )
    data["fo_bhav"]["date"] = pd.to_datetime(data["fo_bhav"]["date"]).dt.date
    if "expiry" in data["fo_bhav"].columns:
        data["fo_bhav"]["expiry"] = pd.to_datetime(data["fo_bhav"]["expiry"]).dt.date

    print("  Loading nse_cm_bhavcopy (aggregated per date + detail)...")
    # For breadth: per-stock close vs prev_close, traded_value, num_trades, volume
    data["cm_bhav"] = _read_sql(
        """SELECT isin, date, close, prev_close, traded_value, num_trades, volume
           FROM nse_cm_bhavcopy
           ORDER BY date"""
    )
    data["cm_bhav"]["date"] = pd.to_datetime(data["cm_bhav"]["date"]).dt.date

    print("  Loading nseix_overnight_fo (NIFTY futures)...")
    data["nseix_fo"] = _read_sql(
        """SELECT date, instrument_type, symbol, expiry, open, high, low, close,
                  settlement, prev_settlement, oi, volume, num_trades, traded_value
           FROM nseix_overnight_fo
           WHERE symbol = 'NIFTY' AND instrument_type = 'FUTIDX'
           ORDER BY date, expiry"""
    )
    data["nseix_fo"]["date"] = pd.to_datetime(data["nseix_fo"]["date"]).dt.date
    if "expiry" in data["nseix_fo"].columns:
        data["nseix_fo"]["expiry"] = pd.to_datetime(data["nseix_fo"]["expiry"]).dt.date

    print("  Loading nseix_overnight_vol (NIFTY)...")
    data["nseix_vol"] = _read_sql(
        """SELECT date, applicable_ann_vol, current_underlying_vol, underlying_ann_vol
           FROM nseix_overnight_vol
           WHERE symbol = 'NIFTY'
           ORDER BY date"""
    )
    data["nseix_vol"]["date"] = pd.to_datetime(data["nseix_vol"]["date"]).dt.date

    print("  Loading nse_fii_dii_participant...")
    data["fii_dii"] = _read_sql(
        """SELECT date,
                  fii_fut_idx_long, fii_fut_idx_short,
                  fii_fut_stk_long, fii_fut_stk_short,
                  fii_opt_idx_call_long, fii_opt_idx_put_long,
                  fii_opt_idx_call_short, fii_opt_idx_put_short,
                  fii_total_long, fii_total_short,
                  dii_total_long, dii_total_short,
                  client_total_long, client_total_short
           FROM nse_fii_dii_participant
           ORDER BY date"""
    )
    data["fii_dii"]["date"] = pd.to_datetime(data["fii_dii"]["date"]).dt.date

    print("  Loading S&P 500 and USD/INR...")
    data["sp500"] = load_sp500()
    data["usdinr"] = load_usdinr()

    elapsed = time.time() - t0
    print(f"  All data loaded in {elapsed:.1f}s")
    return data


# ---------------------------------------------------------------------------
# Pre-compute lookup structures
# ---------------------------------------------------------------------------

def build_lookups(data: dict) -> dict:
    """Build date-indexed lookup structures for fast per-date access."""
    lu = {}

    # Nifty 50 close series (sorted)
    nifty = data["indices"][data["indices"]["index"] == "Nifty 50"].copy()
    nifty = nifty.sort_values("date").reset_index(drop=True)
    lu["nifty"] = nifty
    lu["nifty_dates"] = nifty["date"].tolist()
    lu["nifty_close"] = dict(zip(nifty["date"], nifty["close"].astype(float)))
    lu["nifty_high"] = dict(zip(nifty["date"], nifty["high"].astype(float)))
    lu["nifty_low"] = dict(zip(nifty["date"], nifty["low"].astype(float)))

    # VIX close series
    vix = data["indices"][data["indices"]["index"] == "India VIX"].copy()
    vix = vix.sort_values("date").reset_index(drop=True)
    lu["vix_close"] = dict(zip(vix["date"], vix["close"].astype(float)))
    lu["vix_dates"] = vix["date"].tolist()

    # All indices: index -> sorted list of (date, close)
    lu["idx_returns"] = {}
    for idx_name in data["indices"]["index"].unique():
        idf = data["indices"][data["indices"]["index"] == idx_name].sort_values("date")
        closes = idf["close"].astype(float).values
        dates = idf["date"].values
        rets = np.empty(len(closes))
        rets[0] = np.nan
        rets[1:] = (closes[1:] - closes[:-1]) / closes[:-1]
        lu["idx_returns"][idx_name] = dict(zip(dates, rets))

    # FO bhavcopy grouped by date
    lu["fo_by_date"] = {d: g for d, g in data["fo_bhav"].groupby("date")}

    # CM bhavcopy grouped by date
    lu["cm_by_date"] = {d: g for d, g in data["cm_bhav"].groupby("date")}

    # CM aggregated: per-date trade_intensity and turnover
    cm = data["cm_bhav"]
    cm_agg = cm.groupby("date").agg(
        total_num_trades=("num_trades", "sum"),
        total_volume=("volume", "sum"),
    ).reset_index()
    cm_agg["trade_intensity"] = cm_agg["total_num_trades"].astype(float) / cm_agg["total_volume"].replace(0, np.nan).astype(float)
    cm_agg = cm_agg.sort_values("date")
    lu["cm_intensity"] = cm_agg.set_index("date")["trade_intensity"]

    # NSEIX FO: select near-month contract per date
    nseix_rows = []
    for d, group in data["nseix_fo"].groupby("date"):
        expiries = sorted(group["expiry"].unique())
        if not expiries:
            continue
        nearest = expiries[0]
        # trading_days_until: approximate from nifty trading dates
        td = _approx_trading_days(d, nearest, lu["nifty_dates"])
        if td < 3 and len(expiries) > 1:
            nearest = expiries[1]
        row = group[group["expiry"] == nearest].iloc[0]
        nseix_rows.append(row)
    lu["nseix_fo_near"] = pd.DataFrame(nseix_rows).reset_index(drop=True) if nseix_rows else pd.DataFrame()
    if not lu["nseix_fo_near"].empty:
        lu["nseix_fo_near"]["date"] = pd.to_datetime(lu["nseix_fo_near"]["date"]).dt.date if hasattr(lu["nseix_fo_near"]["date"].iloc[0], 'date') else lu["nseix_fo_near"]["date"]
        lu["nseix_fo_dates"] = lu["nseix_fo_near"]["date"].tolist()
    else:
        lu["nseix_fo_dates"] = []

    # NSEIX vol
    lu["nseix_vol"] = data["nseix_vol"].sort_values("date").reset_index(drop=True)

    # FII/DII by date
    fii = data["fii_dii"].sort_values("date").reset_index(drop=True)
    lu["fii_dii"] = fii
    lu["fii_dii_by_date"] = fii.set_index("date")

    # Ground truth: prev coincident regime
    gt = data["ground_truth"]
    label_map = {"Trend-Up": 2.0, "Range": 1.0, "Trend-Down": 0.0}
    lu["gt_regime"] = {
        row["date"]: label_map.get(row["coincident_label"], 1.0)
        for _, row in gt.iterrows()
        if row["coincident_label"] is not None
    }

    # Nifty close as sorted series for return lookups
    lu["nifty_close_series"] = nifty.set_index("date")["close"].astype(float)

    # Trading dates set (from cm_bhavcopy) for expiry calculations
    lu["trading_dates_set"] = set(data["cm_bhav"]["date"].unique())
    lu["trading_dates_sorted"] = sorted(lu["trading_dates_set"])

    # Previous trading day map (from nifty dates)
    nifty_dates = lu["nifty_dates"]
    prev_map = {}
    for i, d in enumerate(nifty_dates):
        if i > 0:
            prev_map[d] = nifty_dates[i - 1]
    lu["prev_trading_day"] = prev_map

    # FO futures: per-date total OI and volume for near-month NIFTY futures
    # Pre-compute for the fut OI change and volume ratio features
    fo = data["fo_bhav"]
    futs = fo[fo["instrument_type"].isin(["FUTIDX", "IDF"])].copy()
    fut_by_date = {}
    for d, g in futs.groupby("date"):
        expiries = sorted(g["expiry"].unique())
        if expiries:
            near_exp = expiries[0]
            near_g = g[g["expiry"] == near_exp]
            fut_by_date[d] = {
                "close": float(near_g["close"].dropna().iloc[0]) if not near_g["close"].dropna().empty else None,
                "oi": float(near_g["oi"].sum()),
                "volume": float(near_g["volume"].sum()),
                "expiry": near_exp,
            }
    lu["fut_by_date"] = fut_by_date

    # FO daily futures volume for 20d avg
    fut_vol_series = pd.Series({d: v["volume"] for d, v in fut_by_date.items()}).sort_index()
    lu["fut_vol_series"] = fut_vol_series

    return lu


def _approx_trading_days(start: date, end: date, trading_dates: list) -> int:
    """Approximate trading days between start and end using sorted trading_dates list."""
    if start >= end:
        return 0
    # Binary search for positions
    import bisect
    lo = bisect.bisect_right(trading_dates, start)
    hi = bisect.bisect_right(trading_dates, end)
    return hi - lo


# ---------------------------------------------------------------------------
# Feature computation (all in-memory)
# ---------------------------------------------------------------------------

def compute_features_batch(target_date: date, lu: dict, sp500: pd.DataFrame, usdinr: pd.DataFrame) -> dict:
    """Compute all 39 features for target_date using pre-loaded lookups."""
    features = {}

    prev_date = lu["prev_trading_day"].get(target_date)

    # Prev Nifty close
    prev_nifty_close = lu["nifty_close"].get(prev_date) if prev_date else None

    # --- Tier 0: GIFT Nifty Overnight ---
    features.update(_tier0_batch(target_date, prev_nifty_close, lu))

    # --- Tier 1: Previous Day EOD ---
    features.update(_tier1_batch(target_date, prev_date, lu))

    # --- Tier 2: Global Overnight ---
    features["sp500_overnight_return"] = _safe_float(get_sp500_overnight_return(target_date, sp500))
    features["usdinr_overnight_change"] = _safe_float(get_usdinr_overnight_change(target_date, usdinr))

    # --- Tier 3: Calendar/Context ---
    features.update(_tier3_batch(target_date, prev_date, lu))

    # --- Tier 4: F&O structure, index breadth, CM microstructure ---
    features.update(_tier4_batch(target_date, prev_date, lu))

    return features


def _tier0_batch(target_date: date, prev_nifty_close: float | None, lu: dict) -> dict:
    """Tier 0: GIFT Nifty overnight features from pre-loaded NSEIX data."""
    features = {
        "gift_overnight_gap_pct": None,
        "gift_overnight_range_pct": None,
        "gift_overnight_oi_change_pct": None,
        "gift_overnight_volume_conviction": None,
        "gift_overnight_vol_delta": None,
    }

    nseix_fo = lu["nseix_fo_near"]
    if nseix_fo.empty:
        return features

    # Filter to data up to target_date
    mask = nseix_fo["date"] <= target_date
    nseix_sub = nseix_fo[mask]
    if nseix_sub.empty:
        return features

    latest = nseix_sub.iloc[-1]
    latest_date = latest["date"]
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
    if len(nseix_sub) >= 2:
        today_oi = latest.get("oi")
        prev_oi = nseix_sub.iloc[-2].get("oi")
        if all(v is not None and not pd.isna(v) for v in [today_oi, prev_oi]) and float(prev_oi) > 0:
            features["gift_overnight_oi_change_pct"] = _safe_float(
                (float(today_oi) - float(prev_oi)) / float(prev_oi) * 100
            )

    # 4. gift_overnight_volume_conviction (vs 20d avg)
    today_vol = latest.get("volume")
    if today_vol is not None and not pd.isna(today_vol) and len(nseix_sub) > 1:
        vol_series = nseix_sub["volume"].astype(float).dropna()
        window = min(20, len(vol_series) - 1)
        if window > 0:
            avg_vol = vol_series.iloc[-window - 1:-1].mean()
            if avg_vol > 0 and not np.isnan(avg_vol):
                features["gift_overnight_volume_conviction"] = _safe_float(
                    float(today_vol) / avg_vol
                )

    # 5. gift_overnight_vol_delta (EWMA vol change)
    nseix_vol = lu["nseix_vol"]
    vol_sub = nseix_vol[nseix_vol["date"] <= target_date]
    if len(vol_sub) >= 2:
        today_ann = vol_sub.iloc[-1].get("applicable_ann_vol")
        prev_ann = vol_sub.iloc[-2].get("applicable_ann_vol")
        if all(v is not None and not pd.isna(v) for v in [today_ann, prev_ann]):
            features["gift_overnight_vol_delta"] = _safe_float(
                float(today_ann) - float(prev_ann)
            )

    return features


def _tier1_batch(target_date: date, prev_date: date | None, lu: dict) -> dict:
    """Tier 1: Previous day EOD features."""
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

    # --- Nifty returns (1d, 5d, 20d) ---
    nifty_close = lu["nifty_close_series"]
    nifty_up_to = nifty_close[nifty_close.index <= prev_date]
    if len(nifty_up_to) >= 2:
        closes = nifty_up_to.values
        features["prev_nifty_return"] = _safe_float((closes[-1] - closes[-2]) / closes[-2])
        if len(closes) >= 6:
            features["prev_nifty_return_5d"] = _safe_float((closes[-1] - closes[-6]) / closes[-6])
        if len(closes) >= 21:
            features["prev_nifty_return_20d"] = _safe_float((closes[-1] - closes[-21]) / closes[-21])

    # --- VIX ---
    vix_dates = lu["vix_dates"]
    vix_close = lu["vix_close"]
    # Find last 2 VIX dates <= prev_date
    vix_prior = [d for d in vix_dates if d <= prev_date]
    if vix_prior:
        features["prev_vix_close"] = _safe_float(vix_close[vix_prior[-1]])
        if len(vix_prior) >= 2:
            curr_vix = float(vix_close[vix_prior[-1]])
            prev_vix = float(vix_close[vix_prior[-2]])
            if prev_vix > 0:
                features["prev_vix_change_pct"] = _safe_float(
                    (curr_vix - prev_vix) / prev_vix * 100
                )

    # --- Breadth (AD ratio, turnover-weighted) ---
    cm_day = lu["cm_by_date"].get(prev_date)
    if cm_day is not None and not cm_day.empty:
        advances = (cm_day["close"] > cm_day["prev_close"]).sum()
        declines = (cm_day["close"] < cm_day["prev_close"]).sum()
        total = advances + declines
        if total > 0:
            features["prev_ad_ratio"] = _safe_float(advances / total)

        if "traded_value" in cm_day.columns:
            tv = cm_day["traded_value"].fillna(0).astype(float)
            up_mask = cm_day["close"] > cm_day["prev_close"]
            total_tv = tv.sum()
            if total_tv > 0:
                features["prev_breadth_turnover_weighted"] = _safe_float(
                    tv[up_mask].sum() / total_tv
                )

    # --- PCR and Max Pain ---
    fo_day = lu["fo_by_date"].get(prev_date)
    if fo_day is not None and not fo_day.empty:
        opts = fo_day[fo_day["instrument_type"].isin(["IDO", "OPTIDX"])]
        if not opts.empty:
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
                underlying = fo_day["underlying"].dropna()
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

    # --- FII/DII ---
    try:
        if prev_date in lu["fii_dii_by_date"].index:
            latest_fii = lu["fii_dii_by_date"].loc[prev_date]
            # Handle case where there are multiple rows for same date
            if isinstance(latest_fii, pd.DataFrame):
                latest_fii = latest_fii.iloc[-1]
            fii_idx_net = float(latest_fii["fii_fut_idx_long"]) - float(latest_fii["fii_fut_idx_short"])
            features["prev_fii_net_idx_fut"] = _safe_float(fii_idx_net)
            features["prev_fii_net_total"] = _safe_float(
                float(latest_fii["fii_total_long"]) - float(latest_fii["fii_total_short"])
            )
            features["prev_dii_net_total"] = _safe_float(
                float(latest_fii["dii_total_long"]) - float(latest_fii["dii_total_short"])
            )
            call_total = float(latest_fii["fii_opt_idx_call_long"]) + float(latest_fii["fii_opt_idx_call_short"])
            put_total = float(latest_fii["fii_opt_idx_put_long"]) + float(latest_fii["fii_opt_idx_put_short"])
            denom = call_total + put_total
            if denom > 0:
                features["prev_fii_options_skew"] = _safe_float(call_total / denom)
    except (KeyError, IndexError):
        pass

    # --- Index divergence ---
    nifty_ret = features["prev_nifty_return"]
    for idx_name, feat_key in [
        ("Nifty 500", "prev_index_divergence_500"),
        ("Nifty Midcap 150", "prev_index_divergence_midcap"),
    ]:
        idx_rets = lu["idx_returns"].get(idx_name, {})
        idx_ret = idx_rets.get(prev_date)
        if idx_ret is not None and not np.isnan(idx_ret) and nifty_ret is not None:
            features[feat_key] = _safe_float(idx_ret - nifty_ret)

    # --- Previous coincident regime ---
    features["prev_coincident_regime"] = lu["gt_regime"].get(prev_date)

    return features


def _tier3_batch(target_date: date, prev_date: date | None, lu: dict) -> dict:
    """Tier 3: Calendar/Context features."""
    features = {
        "day_of_week": float(target_date.weekday()),
        "days_to_monthly_expiry": None,
        "is_expiry_week": None,
        "prev_day_range_pct": None,
    }

    # Days to monthly expiry
    exp = _next_monthly_expiry(target_date)
    days_to_exp = _approx_trading_days(target_date, exp, lu["trading_dates_sorted"])
    features["days_to_monthly_expiry"] = float(days_to_exp)
    features["is_expiry_week"] = 1.0 if days_to_exp <= 5 else 0.0

    # Previous day range
    if prev_date is not None:
        h = lu["nifty_high"].get(prev_date)
        lo = lu["nifty_low"].get(prev_date)
        c = lu["nifty_close"].get(prev_date)
        if h is not None and lo is not None and c is not None and c > 0:
            features["prev_day_range_pct"] = _safe_float((h - lo) / c * 100)

    return features


def _tier4_batch(target_date: date, prev_date: date | None, lu: dict) -> dict:
    """Tier 4: F&O structure, index breadth, CM microstructure."""
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

    # ---- F&O features ----
    _fo_features_batch(features, prev_date, lu)

    # ---- Index relative features ----
    _index_relative_batch(features, prev_date, lu)

    # ---- CM microstructure ----
    _cm_micro_batch(features, prev_date, lu)

    return features


def _fo_features_batch(features: dict, prev_date: date, lu: dict) -> None:
    """F&O features from pre-loaded fo_bhav data."""
    fo_day = lu["fo_by_date"].get(prev_date)
    if fo_day is None or fo_day.empty:
        return

    underlying = fo_day["underlying"].dropna()
    spot = float(underlying.iloc[0]) if not underlying.empty else None
    if spot is None or spot <= 0:
        return

    # Near-month futures
    fut_info = lu["fut_by_date"].get(prev_date)
    if fut_info:
        # 1. Futures basis %
        fut_close = fut_info["close"]
        if fut_close is not None:
            features["prev_nifty_futures_basis_pct"] = _safe_float(
                (fut_close - spot) / spot * 100
            )

        # 2. Futures OI change %
        fut_oi_today = fut_info["oi"]
        if fut_oi_today and fut_oi_today > 0:
            # Find previous date's OI for same expiry
            prev_prev = lu["prev_trading_day"].get(prev_date)
            if prev_prev:
                prev_fut = lu["fut_by_date"].get(prev_prev)
                if prev_fut and prev_fut["oi"] and prev_fut["oi"] > 0:
                    features["prev_nifty_fut_oi_change_pct"] = _safe_float(
                        (fut_oi_today - prev_fut["oi"]) / prev_fut["oi"] * 100
                    )

        # 3. Futures volume ratio (vs 20d avg)
        fut_vol_today = fut_info["volume"]
        if fut_vol_today and fut_vol_today > 0:
            vol_series = lu["fut_vol_series"]
            hist = vol_series[(vol_series.index < prev_date)].tail(20)
            if not hist.empty:
                avg_vol = hist.mean()
                if avg_vol > 0:
                    features["prev_nifty_fut_volume_ratio"] = _safe_float(
                        fut_vol_today / avg_vol
                    )

    # Options: PCR change, max OI call/put distance
    opts = fo_day[fo_day["instrument_type"].isin(["IDO", "OPTIDX"])]
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

    # 4. PCR OI change
    if call_oi > 0:
        pcr_today = float(put_oi) / float(call_oi)
        # Find previous date's PCR
        prev_prev = lu["prev_trading_day"].get(prev_date)
        if prev_prev:
            prev_fo = lu["fo_by_date"].get(prev_prev)
            if prev_fo is not None and not prev_fo.empty:
                prev_opts = prev_fo[prev_fo["instrument_type"].isin(["IDO", "OPTIDX"])]
                if not prev_opts.empty:
                    prev_exp_opts = prev_opts[prev_opts["expiry"] == near_expiry]
                    if not prev_exp_opts.empty:
                        prev_calls = prev_exp_opts[prev_exp_opts["option_type"] == "CE"]
                        prev_puts = prev_exp_opts[prev_exp_opts["option_type"] == "PE"]
                        prev_call_oi = prev_calls["oi"].sum()
                        if prev_call_oi > 0:
                            pcr_prev = float(prev_puts["oi"].sum()) / float(prev_call_oi)
                            features["prev_pcr_oi_change"] = _safe_float(pcr_today - pcr_prev)

    # 5. Max OI call distance %
    if not calls.empty and spot > 0:
        max_call_strike = float(calls.loc[calls["oi"].idxmax(), "strike"])
        features["prev_max_oi_call_distance_pct"] = _safe_float(
            (max_call_strike - spot) / spot * 100
        )

    # 6. Max OI put distance %
    if not puts.empty and spot > 0:
        max_put_strike = float(puts.loc[puts["oi"].idxmax(), "strike"])
        features["prev_max_oi_put_distance_pct"] = _safe_float(
            (spot - max_put_strike) / spot * 100
        )


def _index_relative_batch(features: dict, prev_date: date, lu: dict) -> None:
    """Index-relative return features from pre-loaded index data."""
    nifty_ret = lu["idx_returns"].get("Nifty 50", {}).get(prev_date)
    if nifty_ret is None or np.isnan(nifty_ret):
        return

    for idx_name, feat_key in [
        ("Nifty Midcap 150", "prev_midcap_vs_nifty"),
        ("Nifty Smallcap 250", "prev_smallcap_vs_nifty"),
        ("Nifty Bank", "prev_bank_vs_nifty"),
    ]:
        idx_ret = lu["idx_returns"].get(idx_name, {}).get(prev_date)
        if idx_ret is not None and not np.isnan(idx_ret):
            features[feat_key] = _safe_float(idx_ret - nifty_ret)

    # Defensive vs Cyclical
    defensive = []
    for n in ["Nifty FMCG", "Nifty Pharma", "Nifty IT"]:
        r = lu["idx_returns"].get(n, {}).get(prev_date)
        if r is not None and not np.isnan(r):
            defensive.append(r)
    cyclical = []
    for n in ["Nifty Metal", "Nifty Auto", "Nifty Realty"]:
        r = lu["idx_returns"].get(n, {}).get(prev_date)
        if r is not None and not np.isnan(r):
            cyclical.append(r)
    if defensive and cyclical:
        features["prev_defensive_vs_cyclical"] = _safe_float(
            np.mean(defensive) - np.mean(cyclical)
        )


def _cm_micro_batch(features: dict, prev_date: date, lu: dict) -> None:
    """CM microstructure features from pre-loaded data."""
    # 11. Trade intensity vs 20d avg
    intensity = lu["cm_intensity"]
    if prev_date in intensity.index:
        curr_val = intensity[prev_date]
        if not np.isnan(curr_val):
            hist = intensity[(intensity.index < prev_date)].tail(20)
            if not hist.empty:
                avg_val = hist.mean()
                if avg_val > 0 and not np.isnan(avg_val):
                    features["prev_trade_intensity"] = _safe_float(curr_val / avg_val)

    # 12. Top-10 turnover share
    cm_day = lu["cm_by_date"].get(prev_date)
    if cm_day is not None and not cm_day.empty and "traded_value" in cm_day.columns:
        tv = cm_day["traded_value"].dropna().astype(float)
        if len(tv) >= 10:
            total_tv = tv.sum()
            top10_tv = tv.nlargest(10).sum()
            if total_tv > 0:
                features["prev_turnover_top10_share"] = _safe_float(top10_tv / total_tv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _last_thursday_of_month(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != 3:
        d -= timedelta(days=1)
    return d


def _next_monthly_expiry(target_date: date) -> date:
    exp = _last_thursday_of_month(target_date.year, target_date.month)
    if exp < target_date:
        if target_date.month == 12:
            exp = _last_thursday_of_month(target_date.year + 1, 1)
        else:
            exp = _last_thursday_of_month(target_date.year, target_date.month + 1)
    return exp


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    t_start = time.time()

    print("Loading global data...")
    sp500 = load_sp500()
    usdinr = load_usdinr()

    print("Loading all DB data (batch)...")
    data = load_all_data()

    print("Building lookup structures...")
    lu = build_lookups(data)
    # Free raw data
    del data

    # Get trading dates from regime_ground_truth
    dates_df = _read_sql("SELECT DISTINCT date FROM regime_ground_truth ORDER BY date")
    dates = [d.date() if hasattr(d, 'date') else d for d in dates_df["date"]]
    print(f"Computing features for {len(dates)} dates...")

    # Nifty return and breadth maps (same as original rebuild script)
    nifty_close = lu["nifty_close_series"]
    nifty_return = nifty_close.pct_change()
    nifty_return_map = {d: float(v) for d, v in nifty_return.items() if not np.isnan(v)}

    # Breadth ratio per date (from pre-loaded cm_by_date)
    breadth_map = {}
    for d, cm_day in lu["cm_by_date"].items():
        if cm_day.empty:
            continue
        adv = (cm_day["close"] > cm_day["prev_close"]).sum()
        dec = (cm_day["close"] < cm_day["prev_close"]).sum()
        total = adv + dec
        if total > 0:
            breadth_map[d] = adv / total

    rows = []
    for i, d in enumerate(dates):
        if i % 100 == 0:
            elapsed = time.time() - t_start
            print(f"  [{elapsed:.0f}s] Computing: {i}/{len(dates)} ({d})")
        try:
            feats = compute_features_batch(d, lu, sp500, usdinr)
            feats["date"] = d
            feats["nifty_return"] = nifty_return_map.get(d)
            feats["breadth_ratio"] = breadth_map.get(d)
            rows.append(feats)
        except Exception as e:
            print(f"  ERROR on {d}: {e}")
            continue

    df = pd.DataFrame(rows)
    out = Path(__file__).resolve().parent.parent / "data" / "preopen_feature_matrix_v2.csv"
    df.to_csv(out, index=False)
    elapsed = time.time() - t_start
    print(f"\nSaved {len(df)} rows x {len(df.columns)} cols to {out}")
    print(f"Total time: {elapsed:.1f}s")

    # Feature coverage
    print("\nFeature coverage:")
    for col in PREOPEN_FEATURE_COLS:
        if col in df.columns:
            nn = df[col].notna().sum()
            print(f"  {col:<40} {nn:>5}/{len(df)} ({nn/len(df)*100:.1f}%)")
        else:
            print(f"  {col:<40} MISSING!")


if __name__ == "__main__":
    main()
