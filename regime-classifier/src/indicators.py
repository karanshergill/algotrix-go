"""TA-Lib wrappers + custom breadth/sentiment indicator calculations.

All functions take pandas DataFrames/Series and return scalar or Series values.
Anti-leakage: every function only uses data up to the target date (enforced by callers).
"""

import numpy as np
import pandas as pd
import talib

from src.config import (
    ADX_PERIOD,
    ATR_PERIOD,
    BB_PERIOD,
    BB_STDDEV,
    EMA_PERIOD,
    HURST_WINDOW,
    PERCENTILE_WINDOW,
)


# ---------------------------------------------------------------------------
# Volatility indicators
# ---------------------------------------------------------------------------


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = ATR_PERIOD) -> pd.Series:
    """Average True Range via TA-Lib."""
    return pd.Series(
        talib.ATR(high.values, low.values, close.values, timeperiod=period),
        index=high.index,
    )


def compute_atr_percentile(atr_series: pd.Series, window: int = PERCENTILE_WINDOW) -> pd.Series:
    """Rolling percentile rank of ATR over window days."""
    return atr_series.rolling(window).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False
    )


def compute_bbw(close: pd.Series, period: int = BB_PERIOD, nbdev: float = BB_STDDEV) -> pd.Series:
    """Bollinger Band Width = (upper - lower) / middle * 100."""
    upper, middle, lower = talib.BBANDS(
        close.values, timeperiod=period, nbdevup=nbdev, nbdevdn=nbdev
    )
    middle_safe = np.where(middle == 0, np.nan, middle)
    return pd.Series((upper - lower) / middle_safe * 100, index=close.index)


def compute_bbw_percentile(bbw_series: pd.Series, window: int = PERCENTILE_WINDOW) -> pd.Series:
    """Rolling percentile rank of BBW over window days."""
    return bbw_series.rolling(window).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False
    )


def compute_vix_roc_5d(vix_series: pd.Series) -> float:
    """VIX 5-day rate of change: (today - 5d ago) / 5d ago * 100."""
    if len(vix_series) < 6:
        return np.nan
    today = vix_series.iloc[-1]
    ago = vix_series.iloc[-6]
    if ago == 0 or np.isnan(ago) or np.isnan(today):
        return np.nan
    return (today - ago) / ago * 100


def compute_yang_zhang_vol(
    open_s: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20
) -> float:
    """Yang-Zhang volatility estimator (handles overnight jumps).
    Returns annualized vol as percentage for the last `window` bars.
    """
    if len(close) < window + 1:
        return np.nan
    o = open_s.iloc[-(window + 1):].values.astype(float)
    h = high.iloc[-(window + 1):].values.astype(float)
    l = low.iloc[-(window + 1):].values.astype(float)
    c = close.iloc[-(window + 1):].values.astype(float)

    # log returns
    log_oc = np.log(o[1:] / c[:-1])  # overnight
    log_co = np.log(c[1:] / o[1:])   # close-to-open (intraday directional)
    log_ho = np.log(h[1:] / o[1:])
    log_lo = np.log(l[1:] / o[1:])

    n = len(log_oc)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))

    # Rogers-Satchell component
    rs = np.mean(log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co))

    sigma_o = np.var(log_oc, ddof=1)
    sigma_c = np.var(log_co, ddof=1)

    yz_var = sigma_o + k * sigma_c + (1 - k) * rs
    if yz_var < 0:
        yz_var = abs(yz_var)
    return float(np.sqrt(yz_var * 252) * 100)


def compute_garman_klass_vol(
    open_s: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20
) -> float:
    """Garman-Klass volatility estimator.
    Returns annualized vol as percentage for the last `window` bars.
    """
    if len(close) < window:
        return np.nan
    o = open_s.iloc[-window:].values.astype(float)
    h = high.iloc[-window:].values.astype(float)
    l = low.iloc[-window:].values.astype(float)
    c = close.iloc[-window:].values.astype(float)

    log_hl = np.log(h / l)
    log_co = np.log(c / o)

    gk = np.mean(0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2)
    if gk < 0:
        gk = abs(gk)
    return float(np.sqrt(gk * 252) * 100)


# ---------------------------------------------------------------------------
# Trend indicators
# ---------------------------------------------------------------------------


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = ADX_PERIOD) -> pd.Series:
    """Average Directional Index via TA-Lib."""
    return pd.Series(
        talib.ADX(high.values, low.values, close.values, timeperiod=period),
        index=high.index,
    )


def compute_ema(close: pd.Series, period: int = EMA_PERIOD) -> pd.Series:
    """Exponential Moving Average via TA-Lib."""
    return pd.Series(talib.EMA(close.values, timeperiod=period), index=close.index)


def compute_ema_distance_pct(close: pd.Series, ema: pd.Series) -> pd.Series:
    """Distance from EMA as percentage: (close - ema) / ema * 100."""
    ema_safe = ema.replace(0, np.nan)
    return (close - ema) / ema_safe * 100


def compute_ema_slope(ema: pd.Series, window: int = 5) -> pd.Series:
    """Slope of EMA over window days (linear regression slope, annualized as pct)."""
    return ema.pct_change(window) * 100


def is_above_ema(close: pd.Series, ema: pd.Series) -> pd.Series:
    """Boolean: is close above EMA?"""
    return close > ema


def compute_nifty_return_5d(close: pd.Series) -> float:
    """5-day cumulative return as percentage."""
    if len(close) < 6:
        return np.nan
    return (close.iloc[-1] / close.iloc[-6] - 1) * 100


def compute_breadth_momentum_5d(
    cm_data: pd.DataFrame, target_date, ema_period: int = EMA_PERIOD
) -> float:
    """5-day delta in universe_pct_above_ema20.
    cm_data must cover at least ema_period+5 days of trading.
    """
    dates = sorted(cm_data["date"].unique())
    dates = [d for d in dates if d <= target_date]
    if len(dates) < ema_period + 6:
        return np.nan

    pivot = cm_data.pivot_table(index="date", columns="isin", values="close")
    pivot = pivot.sort_index()

    # Compute pct_above_ema20 for today and 5 trading days ago
    today_idx = dates[-1]
    ago_idx = dates[-6] if len(dates) >= 6 else dates[0]

    def _pct_above(end_date):
        subset = pivot.loc[:end_date].tail(ema_period + 1)
        if len(subset) < ema_period:
            return np.nan
        last_row = subset.iloc[-1]
        ema_vals = subset.apply(lambda col: talib.EMA(col.values, timeperiod=ema_period)[-1])
        above = (last_row > ema_vals).sum()
        total = last_row.notna().sum()
        return (above / total * 100) if total > 0 else np.nan

    pct_today = _pct_above(today_idx)
    pct_ago = _pct_above(ago_idx)

    if np.isnan(pct_today) or np.isnan(pct_ago):
        return np.nan
    return pct_today - pct_ago


# ---------------------------------------------------------------------------
# Participation / Breadth indicators
# ---------------------------------------------------------------------------


def compute_ad_ratio(cm_day: pd.DataFrame) -> float:
    """Advance/Decline ratio for a single day.
    cm_day: DataFrame with columns [isin, close, prev_close] for one date.
    Returns: advances / declines (or NaN if no declines).
    """
    if cm_day.empty:
        return np.nan
    changes = cm_day["close"] - cm_day["prev_close"]
    advances = (changes > 0).sum()
    declines = (changes < 0).sum()
    if declines == 0:
        return np.nan if advances == 0 else float(advances)
    return advances / declines


def compute_trin(cm_day: pd.DataFrame) -> float:
    """TRIN (Arms Index) = (Adv Issues / Dec Issues) / (Adv Volume / Dec Volume).
    TRIN < 1 = bullish, TRIN > 1 = bearish.
    """
    if cm_day.empty:
        return np.nan
    changes = cm_day["close"] - cm_day["prev_close"]
    adv_mask = changes > 0
    dec_mask = changes < 0

    adv_issues = adv_mask.sum()
    dec_issues = dec_mask.sum()
    adv_volume = cm_day.loc[adv_mask, "volume"].sum()
    dec_volume = cm_day.loc[dec_mask, "volume"].sum()

    if dec_issues == 0 or dec_volume == 0 or adv_volume == 0:
        return np.nan

    ad_ratio = adv_issues / dec_issues
    vol_ratio = adv_volume / dec_volume
    return ad_ratio / vol_ratio


def compute_volume_trend_ratio(cm_data: pd.DataFrame, target_date) -> float:
    """Volume trend ratio: avg_volume_5d / avg_volume_20d (universe-wide).
    >1 = expanding volume, <1 = contracting.
    """
    dates = sorted(cm_data["date"].unique())
    dates = [d for d in dates if d <= target_date]
    if len(dates) < 20:
        return np.nan

    recent_5 = dates[-5:]
    recent_20 = dates[-20:]

    vol_5 = cm_data[cm_data["date"].isin(recent_5)]["volume"].sum()
    vol_20 = cm_data[cm_data["date"].isin(recent_20)]["volume"].sum()

    days_5 = len(recent_5)
    days_20 = len(recent_20)

    avg_5 = vol_5 / days_5 if days_5 > 0 else 0
    avg_20 = vol_20 / days_20 if days_20 > 0 else 0

    if avg_20 == 0:
        return np.nan
    return avg_5 / avg_20


def compute_up_volume_ratio(cm_day: pd.DataFrame) -> float:
    """Up-volume ratio: sum(volume where close > open) / total_volume.
    Doji (close == open) excluded from numerator, included in denominator.
    """
    if cm_day.empty:
        return np.nan
    total_vol = cm_day["volume"].sum()
    if total_vol == 0:
        return np.nan
    up_mask = cm_day["close"] > cm_day["open"]
    up_vol = cm_day.loc[up_mask, "volume"].sum()
    return up_vol / total_vol


def compute_pct_above_ema20(close_by_stock: pd.DataFrame, ema_period: int = EMA_PERIOD) -> float:
    """Percentage of stocks trading above their 20-day EMA.
    close_by_stock: DataFrame with columns as ISINs and index as dates.
    Returns percentage for the last date.
    """
    if close_by_stock.empty or len(close_by_stock) < ema_period:
        return np.nan

    last_row = close_by_stock.iloc[-1]
    ema_values = close_by_stock.apply(lambda col: talib.EMA(col.values, timeperiod=ema_period)[-1])
    above = (last_row > ema_values).sum()
    total = last_row.notna().sum()
    if total == 0:
        return np.nan
    return (above / total) * 100


# ---------------------------------------------------------------------------
# Sentiment indicators (F&O)
# ---------------------------------------------------------------------------


def compute_pcr_oi(fo_day: pd.DataFrame) -> float:
    """Put-Call Ratio (OI) for NIFTY index options.
    PCR = Total Put OI / Total Call OI.
    """
    if fo_day.empty:
        return np.nan
    options = fo_day[fo_day["instrument_type"] == "IDO"]
    if options.empty:
        return np.nan
    puts = options[options["option_type"] == "PE"]["oi"].sum()
    calls = options[options["option_type"] == "CE"]["oi"].sum()
    if calls == 0:
        return np.nan
    return puts / calls


def compute_pcr_oi_nearest_expiry(fo_day: pd.DataFrame, target_date) -> float:
    """PCR using nearest expiry only, switching to next month in last 2 trading days before expiry."""
    if fo_day.empty:
        return np.nan
    options = fo_day[fo_day["instrument_type"].isin(["IDO", "OPTIDX"])]
    if options.empty:
        return np.nan

    expiries = sorted(options["expiry"].dropna().unique())
    if not expiries:
        return np.nan

    # Find nearest expiry, but if it's within 2 calendar days, use next
    from datetime import timedelta
    nearest = expiries[0]
    if hasattr(target_date, 'date'):
        td = target_date
    else:
        td = target_date

    days_to_expiry = (nearest - td).days if hasattr(nearest, '__sub__') else 999
    if days_to_expiry <= 2 and len(expiries) > 1:
        nearest = expiries[1]

    exp_options = options[options["expiry"] == nearest]
    puts = exp_options[exp_options["option_type"] == "PE"]["oi"].sum()
    calls = exp_options[exp_options["option_type"] == "CE"]["oi"].sum()
    if calls == 0:
        return np.nan
    return puts / calls


def compute_fii_net_idx_fut_oi(fii_dii_row: pd.Series) -> float:
    """FII net index futures OI: long - short."""
    long_val = fii_dii_row.get("fii_fut_idx_long")
    short_val = fii_dii_row.get("fii_fut_idx_short")
    if long_val is None or short_val is None:
        return np.nan
    return float(long_val - short_val)


def compute_fii_vs_client_ratio(fii_dii_row: pd.Series) -> float:
    """FII net / client net. Clip denominator to abs >= 1000."""
    fii_net = fii_dii_row.get("fii_fut_idx_long", 0) - fii_dii_row.get("fii_fut_idx_short", 0)
    client_net = fii_dii_row.get("client_total_long", 0) - fii_dii_row.get("client_total_short", 0)
    if abs(client_net) < 1000:
        client_net = 1000 if client_net >= 0 else -1000
    return float(fii_net) / float(client_net)


def compute_futures_basis_pct(fo_day: pd.DataFrame) -> float:
    """Futures basis as percentage: (near-month futures close - spot) / spot * 100.
    Positive basis = contango (bullish), negative = backwardation (bearish).
    """
    if fo_day.empty:
        return np.nan
    futures = fo_day[fo_day["instrument_type"] == "IDF"]
    if futures.empty:
        return np.nan
    # Near-month = earliest expiry
    futures = futures.sort_values("expiry")
    near_month = futures.iloc[0]
    spot = near_month.get("underlying")
    fut_close = near_month.get("close")
    if pd.isna(spot) or pd.isna(fut_close) or spot == 0:
        return np.nan
    return (fut_close - spot) / spot * 100


# ---------------------------------------------------------------------------
# Experimental
# ---------------------------------------------------------------------------


def compute_fii_flow_delta(fii_dii_df: pd.DataFrame) -> float:
    """Day-over-day change in FII net index futures OI."""
    if len(fii_dii_df) < 2:
        return np.nan
    today = fii_dii_df.iloc[-1]
    yesterday = fii_dii_df.iloc[-2]
    net_today = today["fii_fut_idx_long"] - today["fii_fut_idx_short"]
    net_yesterday = yesterday["fii_fut_idx_long"] - yesterday["fii_fut_idx_short"]
    return float(net_today - net_yesterday)


def compute_dii_flow_delta(fii_dii_df: pd.DataFrame) -> float:
    """Day-over-day change in DII net index futures OI."""
    if len(fii_dii_df) < 2:
        return np.nan
    today = fii_dii_df.iloc[-1]
    yesterday = fii_dii_df.iloc[-2]
    net_today = today["dii_fut_idx_long"] - today["dii_fut_idx_short"]
    net_yesterday = yesterday["dii_fut_idx_long"] - yesterday["dii_fut_idx_short"]
    return float(net_today - net_yesterday)


def compute_gift_nifty_overnight_gap(ix_settlement: float, prev_nifty_close: float) -> float:
    """GIFT Nifty overnight gap: (IX settlement - prev NSE close) / prev_close * 100."""
    if ix_settlement is None or prev_nifty_close is None or prev_nifty_close == 0:
        return np.nan
    return (ix_settlement - prev_nifty_close) / prev_nifty_close * 100


def compute_overnight_return(prices: pd.Series) -> float:
    """Generic overnight return: (today - yesterday) / yesterday * 100."""
    if len(prices) < 2:
        return np.nan
    today = prices.iloc[-1]
    yesterday = prices.iloc[-2]
    if np.isnan(today) or np.isnan(yesterday) or yesterday == 0:
        return np.nan
    return (today - yesterday) / yesterday * 100


def compute_hurst(close: pd.Series, window: int = HURST_WINDOW) -> float:
    """Hurst exponent via nolds. H>0.5=trending, H<0.5=mean-reverting, H≈0.5=random.
    Uses the last `window` closes.
    """
    import nolds

    if len(close) < window:
        return np.nan
    data = close.iloc[-window:].dropna().values
    if len(data) < window:
        return np.nan
    try:
        return nolds.hurst_rs(data, fit="poly")
    except Exception:
        return np.nan
