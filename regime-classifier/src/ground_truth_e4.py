"""E4 ground truth enrichment — multi-dimensional label confirmation.

Adds 4 new dimensions (D3–D6) on top of E3's price direction + breadth:
  D3: Volume conviction (traded_value vs 20d avg)
  D4: Cross-sectional dispersion (stock return std vs 20d avg)
  D5: Turnover concentration (top-10 share vs 20d avg)
  D6: Sector participation (fraction of 12 sectors agreeing with Nifty direction)

Each dimension scores +1 (confirms trend), 0 (neutral), or -1 (contradicts trend).
E4 label = E3 candidate confirmed/demoted by sum of D3–D6 scores.
"""

import numpy as np
import pandas as pd

# 12 sector indices tracked in nse_indices_daily
SECTOR_INDICES = [
    "Nifty Bank", "Nifty IT", "Nifty Pharma", "Nifty Auto",
    "Nifty Metal", "Nifty FMCG", "Nifty Energy", "Nifty Realty",
    "Nifty Financial Services", "Nifty Infrastructure", "Nifty Media",
    "Nifty PSU Bank",
]

ROLLING_WINDOW = 20


# ---------------------------------------------------------------------------
# D3: Volume Conviction
# ---------------------------------------------------------------------------

def compute_volume_conviction(date, cm_df: pd.DataFrame) -> dict:
    """Score volume conviction for a single date.

    Args:
        date: Target trading date.
        cm_df: Full CM bhavcopy DataFrame with columns [date, traded_value].
               Must contain at least ROLLING_WINDOW prior days.

    Returns:
        dict with keys: d3_score (-1/0/+1), d3_raw (volume_ratio float).
    """
    daily_turnover = cm_df.groupby("date")["traded_value"].sum().sort_index()

    if date not in daily_turnover.index:
        return {"d3_score": 0, "d3_raw": np.nan}

    loc = daily_turnover.index.get_loc(date)
    if loc < 1:
        return {"d3_score": 0, "d3_raw": np.nan}

    start = max(0, loc - ROLLING_WINDOW)
    window = daily_turnover.iloc[start:loc]

    if len(window) == 0:
        return {"d3_score": 0, "d3_raw": np.nan}

    avg_turnover = window.mean()
    today_turnover = daily_turnover.iloc[loc]
    volume_ratio = today_turnover / avg_turnover if avg_turnover > 0 else np.nan

    if np.isnan(volume_ratio):
        score = 0
    elif volume_ratio >= 1.20:
        score = 1
    elif volume_ratio <= 0.80:
        score = -1
    else:
        score = 0

    return {"d3_score": score, "d3_raw": float(volume_ratio)}


# ---------------------------------------------------------------------------
# D4: Cross-Sectional Dispersion
# ---------------------------------------------------------------------------

def compute_dispersion(date, cm_df: pd.DataFrame) -> dict:
    """Score cross-sectional dispersion for a single date.

    Args:
        date: Target trading date.
        cm_df: Full CM bhavcopy DataFrame with columns [date, close, prev_close].

    Returns:
        dict with keys: d4_score (-1/0/+1), d4_raw (dispersion_ratio float).
    """
    # Compute per-stock returns per day
    df = cm_df[cm_df["prev_close"] > 0].copy()
    df["stock_return"] = (df["close"] - df["prev_close"]) / df["prev_close"]

    daily_disp = df.groupby("date")["stock_return"].std().sort_index()

    if date not in daily_disp.index:
        return {"d4_score": 0, "d4_raw": np.nan}

    loc = daily_disp.index.get_loc(date)
    if loc < 1:
        return {"d4_score": 0, "d4_raw": np.nan}

    start = max(0, loc - ROLLING_WINDOW)
    window = daily_disp.iloc[start:loc]

    if len(window) == 0:
        return {"d4_score": 0, "d4_raw": np.nan}

    avg_disp = window.mean()
    today_disp = daily_disp.iloc[loc]
    dispersion_ratio = today_disp / avg_disp if avg_disp > 0 else np.nan

    if np.isnan(dispersion_ratio):
        score = 0
    elif dispersion_ratio <= 0.85:
        score = 1   # low dispersion confirms clean trend
    elif dispersion_ratio >= 1.30:
        score = -1  # high dispersion contradicts trend
    else:
        score = 0

    return {"d4_score": score, "d4_raw": float(dispersion_ratio)}


# ---------------------------------------------------------------------------
# D5: Turnover Concentration
# ---------------------------------------------------------------------------

def compute_turnover_concentration(date, cm_df: pd.DataFrame) -> dict:
    """Score turnover concentration for a single date.

    Args:
        date: Target trading date.
        cm_df: Full CM bhavcopy DataFrame with columns [date, traded_value].

    Returns:
        dict with keys: d5_score (-1/0/+1), d5_raw (concentration_ratio float).
    """
    # Compute top-10 share per day
    def _top10_share(group):
        vals = group["traded_value"].sort_values(ascending=False)
        total = vals.sum()
        if total == 0:
            return np.nan
        top10 = vals.head(10).sum()
        return top10 / total

    daily_conc = cm_df.groupby("date").apply(_top10_share).sort_index()

    if date not in daily_conc.index:
        return {"d5_score": 0, "d5_raw": np.nan}

    loc = daily_conc.index.get_loc(date)
    if loc < 1:
        return {"d5_score": 0, "d5_raw": np.nan}

    start = max(0, loc - ROLLING_WINDOW)
    window = daily_conc.iloc[start:loc]

    if len(window) == 0:
        return {"d5_score": 0, "d5_raw": np.nan}

    avg_conc = window.mean()
    today_conc = daily_conc.iloc[loc]
    concentration_ratio = today_conc / avg_conc if avg_conc > 0 else np.nan

    if np.isnan(concentration_ratio):
        score = 0
    elif concentration_ratio >= 1.15:
        score = -1  # concentrated — few heavyweights driving move
    elif concentration_ratio <= 0.90:
        score = 1   # distributed — broad participation
    else:
        score = 0

    return {"d5_score": score, "d5_raw": float(concentration_ratio)}


# ---------------------------------------------------------------------------
# D6: Sector Participation
# ---------------------------------------------------------------------------

def compute_sector_participation(date, indices_df: pd.DataFrame, nifty_return: float) -> dict:
    """Score sector participation for a single date.

    Args:
        date: Target trading date.
        indices_df: Full indices DataFrame with columns [index, date, close].
                    Must contain rows for SECTOR_INDICES.
        nifty_return: Nifty 50 return on this date (used for direction).

    Returns:
        dict with keys: d6_score (-1/0/+1), d6_raw (participation ratio),
                        d6_sectors_agreeing (int count).
    """
    if np.isnan(nifty_return) or nifty_return == 0:
        return {"d6_score": 0, "d6_raw": np.nan, "d6_sectors_agreeing": 0}

    nifty_direction = np.sign(nifty_return)

    sectors_today = indices_df[indices_df["date"] == date]
    if sectors_today.empty:
        return {"d6_score": 0, "d6_raw": np.nan, "d6_sectors_agreeing": 0}

    agreeing = 0
    counted = 0
    for sector in SECTOR_INDICES:
        row = sectors_today[sectors_today["index"] == sector]
        if row.empty or pd.isna(row.iloc[0].get("sector_return")):
            continue
        counted += 1
        if np.sign(row.iloc[0]["sector_return"]) == nifty_direction:
            agreeing += 1

    if counted == 0:
        return {"d6_score": 0, "d6_raw": np.nan, "d6_sectors_agreeing": 0}

    participation = agreeing / 12.0  # always out of 12 per plan

    if participation >= 0.75:
        score = 1   # broad — 9+ sectors agree
    elif participation <= 0.42:
        score = -1  # narrow — 5 or fewer agree
    else:
        score = 0

    return {"d6_score": score, "d6_raw": float(participation), "d6_sectors_agreeing": agreeing}


# ---------------------------------------------------------------------------
# E4 Label Computation
# ---------------------------------------------------------------------------

def compute_e4_label(e3_label: str, d3_score: int, d4_score: int,
                     d5_score: int, d6_score: int, threshold: int) -> str:
    """Compute E4 label from E3 candidate + D3–D6 confirmation scores.

    Args:
        e3_label: E3 candidate label ("Trend-Up", "Range", "Trend-Down").
        d3_score, d4_score, d5_score, d6_score: Individual dimension scores (-1/0/+1).
        threshold: Minimum confirm_score to keep a trend label.
                   strict=3, moderate=2, loose=0.

    Returns:
        Final E4 label: "Trend-Up", "Range", or "Trend-Down".
    """
    if e3_label == "Range":
        return "Range"

    confirm_score = d3_score + d4_score + d5_score + d6_score

    if confirm_score >= threshold:
        return e3_label
    else:
        return "Range"
