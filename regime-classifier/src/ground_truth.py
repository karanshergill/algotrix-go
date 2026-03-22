"""Ground truth label computation for regime validation.

Two separate targets:
1. Coincident truth — "What kind of day was D?" (E3 percentile-based)
2. Predictive truth — "What actually happened on D+1?" (pure next-day return bucket)
"""

import numpy as np
import pandas as pd


def compute_rolling_stats(df: pd.DataFrame, window: int = 252) -> pd.DataFrame:
    """Compute trailing rolling percentile stats needed for E3 labelling.

    Expects df to have columns: return_pct, cir, breadth_ratio (sorted by date).
    Uses expanding window when fewer than `window` rows are available.

    Returns df with added columns: ret_p33, ret_p67, cir_p33, cir_p67,
    breadth_p33, breadth_p67.
    """
    n = len(df)
    abs_ret = df["return_pct"].abs()

    ret_p33 = np.full(n, np.nan)
    ret_p67 = np.full(n, np.nan)
    cir_p33 = np.full(n, np.nan)
    cir_p67 = np.full(n, np.nan)
    breadth_p33 = np.full(n, np.nan)
    breadth_p67 = np.full(n, np.nan)

    for i in range(1, n):
        start = max(0, i - window)
        win = slice(start, i)

        ret_vals = abs_ret.iloc[win].dropna()
        if len(ret_vals) > 0:
            ret_p33[i] = np.percentile(ret_vals, 33)
            ret_p67[i] = np.percentile(ret_vals, 67)

        cir_vals = df["cir"].iloc[win].dropna()
        if len(cir_vals) > 0:
            cir_p33[i] = np.percentile(cir_vals, 33)
            cir_p67[i] = np.percentile(cir_vals, 67)

        br_vals = df["breadth_ratio"].iloc[win].dropna()
        if len(br_vals) > 0:
            breadth_p33[i] = np.percentile(br_vals, 33)
            breadth_p67[i] = np.percentile(br_vals, 67)

    df["ret_p33"] = ret_p33
    df["ret_p67"] = ret_p67
    df["cir_p33"] = cir_p33
    df["cir_p67"] = cir_p67
    df["breadth_p33"] = breadth_p33
    df["breadth_p67"] = breadth_p67
    return df


def compute_coincident_truth(date, nifty_open, nifty_high, nifty_low, nifty_close,
                              prev_nifty_close, breadth_ratio, vix_close, prev_vix_close,
                              rolling_stats: dict) -> str:
    """Label day D based on E3 percentile-based logic.

    Args:
        date: Trading date.
        nifty_open: Nifty open price on day D.
        nifty_high: Nifty high price on day D.
        nifty_low: Nifty low price on day D.
        nifty_close: Nifty close price on day D.
        prev_nifty_close: Nifty close price on day D-1.
        breadth_ratio: advances / (advances + declines) on day D.
        vix_close: VIX close on day D (unused in E3, kept for interface compat).
        prev_vix_close: VIX close on day D-1 (unused in E3, kept for interface compat).
        rolling_stats: dict with keys ret_p33, ret_p67, cir_p33, cir_p67,
                       breadth_p33, breadth_p67 (trailing 252-day percentiles).

    Returns:
        "Trend-Up", "Range", or "Trend-Down"
    """
    # Compute derived values
    return_pct = (nifty_close / prev_nifty_close) - 1
    day_range = nifty_high - nifty_low
    cir = 0.5 if day_range == 0 else (nifty_close - nifty_low) / day_range

    ret_p33 = rolling_stats["ret_p33"]
    ret_p67 = rolling_stats["ret_p67"]
    cir_p33 = rolling_stats["cir_p33"]
    cir_p67 = rolling_stats["cir_p67"]
    breadth_p33 = rolling_stats["breadth_p33"]
    breadth_p67 = rolling_stats["breadth_p67"]

    # Strong trend with P67 thresholds
    if return_pct > ret_p67 and cir > cir_p67 and (breadth_ratio is None or breadth_ratio > breadth_p67):
        return "Trend-Up"
    if return_pct < -ret_p67 and cir < cir_p33 and (breadth_ratio is None or breadth_ratio < breadth_p33):
        return "Trend-Down"

    # Weaker directional (use midpoint between P33 and P67)
    cir_mid = (cir_p33 + cir_p67) / 2
    if return_pct > ret_p33 and cir > cir_mid:
        return "Trend-Up"
    if return_pct < -ret_p33 and cir < cir_mid:
        return "Trend-Down"

    return "Range"


def compute_predictive_truth(next_day_return: float) -> str:
    """Label for D+1 based purely on next-day Nifty return.
    Independent of scorer dimensions — avoids circularity.

    Args:
        next_day_return: nifty_close[D+1] / nifty_close[D] - 1
    """
    if next_day_return > 0.003:
        return "Trend-Up"
    elif next_day_return < -0.003:
        return "Trend-Down"
    else:
        return "Range"
