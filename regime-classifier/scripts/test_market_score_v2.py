#!/usr/bin/env python3 -u
"""
Market score v2 prototype: adds LEADING indicators to improve next-day prediction.

New features over v1:
  1. Overnight gap (today_open vs yesterday_close)
  2. Volume surge (today volume / 20d avg volume)
  3. Breadth momentum (5d change in % stocks above 20d EMA)
  4. VIX rate of change (5d)
  5. Score momentum (3d change in composite score)
  6. Mean-reversion signal (contrarian bonus at extremes)

Compares two weight schemes:
  A) Equal weight across all feature groups
  B) Heavier weight on leading indicators
"""

import sys
import numpy as np
import pandas as pd
import psycopg2
import talib
from scipy.stats import spearmanr

# ── DB ────────────────────────────────────────────────────────────────────────
DSN = "host=localhost user=me password=algotrix dbname=atdb"

def query(sql: str) -> pd.DataFrame:
    with psycopg2.connect(DSN) as conn:
        return pd.read_sql(sql, conn)

# ── 1. Load data ─────────────────────────────────────────────────────────────
print("Loading Nifty 50 OHLCV …")
nifty = query("""
    SELECT date, open, high, low, close, volume
    FROM nse_indices_daily
    WHERE index = 'Nifty 50'
    ORDER BY date
""").set_index("date")

print("Loading India VIX …")
vix = query("""
    SELECT date, close AS vix_close
    FROM nse_indices_daily
    WHERE index = 'India VIX'
    ORDER BY date
""").set_index("date")

print("Loading advance/decline data …")
ad = query("""
    SELECT date,
           SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) AS advances,
           SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) AS declines,
           COUNT(*) AS total
    FROM nse_cm_bhavcopy
    GROUP BY date
    ORDER BY date
""").set_index("date")

print("Loading per-stock closes for breadth …")
stock_closes = query("""
    SELECT date, isin, close
    FROM nse_cm_bhavcopy
    ORDER BY isin, date
""")

print(f"  Nifty rows: {len(nifty)}, VIX rows: {len(vix)}, AD rows: {len(ad)}")

# ── 2. Compute per-stock 20-day EMA for breadth ──────────────────────────────
print("Computing breadth (% stocks above own 20d EMA) …")
breadth_records = []
for isin, grp in stock_closes.groupby("isin"):
    grp = grp.sort_values("date")
    if len(grp) < 20:
        continue
    ema20 = talib.EMA(grp["close"].values, timeperiod=20)
    for dt, c, e in zip(grp["date"], grp["close"], ema20):
        if np.isnan(e):
            continue
        breadth_records.append((dt, 1 if c > e else 0))

breadth_df = pd.DataFrame(breadth_records, columns=["date", "above"])
breadth = breadth_df.groupby("date")["above"].mean().rename("pct_above_ema20")
breadth.index = pd.to_datetime(breadth.index)
print(f"  Breadth days: {len(breadth)}")

# ── 3. Build feature frame aligned to Nifty trading days ─────────────────────
print("Building features …")
df = nifty.copy()
df.index = pd.to_datetime(df.index)
vix.index = pd.to_datetime(vix.index)
ad.index = pd.to_datetime(ad.index)

# ── Original (v1) features ───────────────────────────────────────────────────
# Trend features
df["ema20"] = talib.EMA(df["close"].values, timeperiod=20)
df["close_vs_ema"] = (df["close"] - df["ema20"]) / df["ema20"] * 100
df["ema20_slope"] = df["ema20"].pct_change(5) * 100
df["adx14"] = talib.ADX(df["high"].values, df["low"].values, df["close"].values, timeperiod=14)

# Participation features
df = df.join(ad[["advances", "declines"]])
df["ad_ratio"] = df["advances"] / df["declines"].replace(0, np.nan)
df = df.join(breadth)

# Sentiment features
df = df.join(vix[["vix_close"]])
df["vix_roc"] = df["vix_close"] / df["vix_close"].rolling(5).mean() - 1

# ── NEW v2 features ──────────────────────────────────────────────────────────

# 1. OVERNIGHT GAP: (today_open - yesterday_close) / yesterday_close * 100
df["overnight_gap"] = (df["open"] - df["close"].shift(1)) / df["close"].shift(1) * 100

# 2. VOLUME SURGE: today_volume / 20-day avg volume
df["vol_sma20"] = df["volume"].rolling(20).mean()
df["volume_surge"] = df["volume"] / df["vol_sma20"].replace(0, np.nan)
# Sign it: positive on up days, negative on down days (for scoring direction)
df["nifty_daily_return"] = df["close"].pct_change() * 100
df["volume_surge_signed"] = df["volume_surge"] * np.sign(df["nifty_daily_return"])

# 3. BREADTH MOMENTUM: 5-day change in pct_above_ema20
df["breadth_momentum"] = df["pct_above_ema20"].diff(5)

# 4. VIX RATE OF CHANGE (5-day, distinct from v1's vix_roc which is vs 5d avg)
df["vix_roc_5d"] = (df["vix_close"] - df["vix_close"].shift(5)) / df["vix_close"].shift(5)

# ── 4. Z-score normalization (60-day rolling) ────────────────────────────────
print("Z-scoring features …")

# All features: (column, invert?)
# invert=True means higher raw = bearish (e.g. VIX)
ALL_FEATURES = {
    # v1 features
    "close_vs_ema":     ("close_vs_ema",     False),
    "ema20_slope":      ("ema20_slope",       False),
    "adx14":            ("adx14",             False),
    "ad_ratio":         ("ad_ratio",          False),
    "pct_above_ema20":  ("pct_above_ema20",   False),
    "vix_close":        ("vix_close",         True),
    "vix_roc":          ("vix_roc",           True),
    # v2 leading features
    "overnight_gap":    ("overnight_gap",     False),   # positive gap = bullish
    "volume_surge":     ("volume_surge_signed", False),  # signed: up+volume=bullish
    "breadth_momentum": ("breadth_momentum",  False),   # improving breadth = bullish
    "vix_roc_5d":       ("vix_roc_5d",        True),    # rising VIX = bearish
}

for name, (col, invert) in ALL_FEATURES.items():
    roll_mean = df[col].rolling(60, min_periods=40).mean()
    roll_std  = df[col].rolling(60, min_periods=40).std()
    z = (df[col] - roll_mean) / roll_std.replace(0, np.nan)
    if invert:
        z = -z
    df[f"z_{name}"] = z

# ADX sign: ADX measures trend *strength* not direction.
df["z_adx14"] = df["z_adx14"] * np.sign(df["z_close_vs_ema"])

# ── 5. Composite scores — two weight schemes ────────────────────────────────
print("Computing composite scores …")

def rolling_percentile_rank(series, window=252):
    result = pd.Series(np.nan, index=series.index)
    vals = series.values
    for i in range(window, len(vals)):
        if np.isnan(vals[i]):
            continue
        lookback = vals[max(0, i - window):i]
        lookback = lookback[~np.isnan(lookback)]
        if len(lookback) < 60:
            continue
        result.iloc[i] = (lookback < vals[i]).sum() / len(lookback) * 100
    return result

# Weight scheme A: Equal weight across feature groups
# Group 1 (Trend): close_vs_ema, ema20_slope, adx14
# Group 2 (Participation): ad_ratio, pct_above_ema20
# Group 3 (Sentiment): vix_close, vix_roc
# Group 4 (Leading): overnight_gap, volume_surge, breadth_momentum, vix_roc_5d
# 4 groups, equal weight → 25% each, split within group
WEIGHTS_A = {
    "close_vs_ema":     0.25 / 3,
    "ema20_slope":      0.25 / 3,
    "adx14":            0.25 / 3,
    "ad_ratio":         0.25 / 2,
    "pct_above_ema20":  0.25 / 2,
    "vix_close":        0.25 / 2,
    "vix_roc":          0.25 / 2,
    "overnight_gap":    0.25 / 4,
    "volume_surge":     0.25 / 4,
    "breadth_momentum": 0.25 / 4,
    "vix_roc_5d":       0.25 / 4,
}

# Weight scheme B: Heavy on leading indicators (40% leading, 20% each for others)
WEIGHTS_B = {
    "close_vs_ema":     0.20 / 3,
    "ema20_slope":      0.20 / 3,
    "adx14":            0.20 / 3,
    "ad_ratio":         0.20 / 2,
    "pct_above_ema20":  0.20 / 2,
    "vix_close":        0.20 / 2,
    "vix_roc":          0.20 / 2,
    "overnight_gap":    0.40 / 4,
    "volume_surge":     0.40 / 4,
    "breadth_momentum": 0.40 / 4,
    "vix_roc_5d":       0.40 / 4,
}

z_cols = [f"z_{name}" for name in ALL_FEATURES]

for scheme_name, weights_dict in [("A", WEIGHTS_A), ("B", WEIGHTS_B)]:
    weights = np.array([weights_dict[name] for name in ALL_FEATURES])
    weights = weights / weights.sum()

    z_matrix = df[z_cols].values
    raw = np.nansum(z_matrix * weights, axis=1)
    # NaN where any feature is NaN
    mask = df[z_cols].isna().any(axis=1)
    raw[mask.values] = np.nan

    df[f"raw_score_{scheme_name}"] = raw
    df[f"market_score_{scheme_name}"] = rolling_percentile_rank(pd.Series(raw, index=df.index))

# ── 6. Score momentum and mean-reversion bonus ──────────────────────────────
print("Adding score momentum & mean-reversion signals …")

for scheme in ["A", "B"]:
    ms = df[f"market_score_{scheme}"]

    # 5. SCORE MOMENTUM: 3-day change in market_score
    df[f"score_delta_3d_{scheme}"] = ms.diff(3)

    # 6. MEAN-REVERSION SIGNAL
    # Bottom 20th percentile → contrarian bonus (+10 to raw score before re-ranking)
    # Top 80th percentile → contrarian penalty (-10)
    # We apply this as an adjustment to the percentile score itself
    mr_bonus = pd.Series(0.0, index=df.index)
    mr_bonus[ms <= 20] = 15.0   # boost low scores
    mr_bonus[ms >= 80] = -15.0  # penalize high scores
    df[f"market_score_mr_{scheme}"] = (ms + mr_bonus).clip(0, 100)

# ── 7. Final scores, labels, next-day return ─────────────────────────────────
print("Building output …")

# Use mean-reversion adjusted scores as the final v2 scores
for scheme in ["A", "B"]:
    final = f"final_score_{scheme}"
    df[final] = df[f"market_score_mr_{scheme}"]

    df[f"regime_{scheme}"] = pd.cut(
        df[final],
        bins=[-0.01, 35, 65, 100.01],
        labels=["Bearish", "Neutral", "Bullish"],
    )

df["next_day_return"] = df["close"].pct_change().shift(-1) * 100

# ── 8. Trim warmup ──────────────────────────────────────────────────────────
out = df[[
    "final_score_A", "final_score_B", "regime_A", "regime_B",
    "market_score_A", "market_score_B",
    "score_delta_3d_A", "score_delta_3d_B",
    "overnight_gap", "volume_surge", "breadth_momentum", "vix_roc_5d",
    "next_day_return",
]].copy()
out = out.dropna(subset=["final_score_A", "final_score_B"])
out.index.name = "date"

# ── 9. Save CSV ──────────────────────────────────────────────────────────────
csv_path = "/home/me/projects/algotrix-go/regime-classifier/scripts/market_score_v2_results.csv"
out.to_csv(csv_path)
print(f"\nSaved {len(out)} rows to {csv_path}")

# ── 10. Validation ───────────────────────────────────────────────────────────
def validate_scheme(df_valid, score_col, regime_col, label):
    """Print full validation for one weight scheme."""
    print(f"\n{'─' * 60}")
    print(f"  SCHEME {label}")
    print(f"{'─' * 60}")

    v = df_valid.dropna(subset=["next_day_return", score_col])

    # Regime counts
    print(f"\n  Total scored days: {len(v)}")
    counts = v[regime_col].value_counts()
    for r in ["Bullish", "Neutral", "Bearish"]:
        n = counts.get(r, 0)
        print(f"    {r:>8s}: {n:>5d} days ({n/len(v)*100:.1f}%)")

    # Avg return by regime
    print(f"\n  Avg next-day Nifty return by regime:")
    grp = v.groupby(regime_col, observed=True)["next_day_return"].agg(["mean", "median", "std", "count"])
    for r in ["Bullish", "Neutral", "Bearish"]:
        if r in grp.index:
            g = grp.loc[r]
            print(f"    {r:>8s}: mean={g['mean']:+.4f}%  median={g['median']:+.4f}%  std={g['std']:.4f}%  n={int(g['count'])}")

    # Correlations
    corr_p = v[score_col].corr(v["next_day_return"])
    rho, pval = spearmanr(v[score_col], v["next_day_return"])
    print(f"\n  Score ↔ next-day return:")
    print(f"    Pearson r  = {corr_p:.4f}")
    print(f"    Spearman ρ = {rho:.4f}  (p={pval:.2e})")

    # Quintile analysis
    print(f"\n  Quintile analysis:")
    v = v.copy()
    v["quintile"] = pd.qcut(v[score_col], 5, labels=["Q1(bear)", "Q2", "Q3", "Q4", "Q5(bull)"], duplicates="drop")
    q_ret = v.groupby("quintile", observed=True)["next_day_return"].agg(["mean", "std", "count"])
    for q in q_ret.index:
        print(f"    {q}: mean={q_ret.loc[q, 'mean']:+.4f}%  std={q_ret.loc[q, 'std']:.4f}%  n={int(q_ret.loc[q, 'count'])}")
    q1_mean = q_ret.iloc[0]["mean"] if len(q_ret) > 0 else 0
    q5_mean = q_ret.iloc[-1]["mean"] if len(q_ret) > 0 else 0
    print(f"    Q1-Q5 spread: {q1_mean - q5_mean:+.4f}%")

    # Hit rate
    bullish_days = v[v[regime_col] == "Bullish"]
    if len(bullish_days) > 0:
        hit_rate = (bullish_days["next_day_return"] > 0).mean() * 100
        print(f"\n  Hit rate (bullish → positive next-day): {hit_rate:.1f}%  ({int((bullish_days['next_day_return'] > 0).sum())}/{len(bullish_days)})")
    bearish_days = v[v[regime_col] == "Bearish"]
    if len(bearish_days) > 0:
        bear_hit = (bearish_days["next_day_return"] < 0).mean() * 100
        print(f"  Hit rate (bearish → negative next-day): {bear_hit:.1f}%  ({int((bearish_days['next_day_return'] < 0).sum())}/{len(bearish_days)})")

    return corr_p, rho


print("\n" + "=" * 60)
print("  MARKET SCORE v2 — VALIDATION RESULTS")
print("=" * 60)
print(f"\nTotal scored trading days: {len(out)}")
print(f"Date range: {out.index.min().date()} to {out.index.max().date()}")

corr_a_p, corr_a_s = validate_scheme(out, "final_score_A", "regime_A", "A (Equal Weight)")
corr_b_p, corr_b_s = validate_scheme(out, "final_score_B", "regime_B", "B (Leading-Heavy)")

# ── 11. v1 baseline comparison ───────────────────────────────────────────────
print(f"\n{'=' * 60}")
print("  v1 vs v2 COMPARISON")
print("=" * 60)
print(f"\n  v1 baseline (from prior run):")
print(f"    Pearson r  = -0.0080  (near zero)")
print(f"    Spearman ρ = -0.0074")
print(f"\n  v2 Scheme A (Equal Weight):")
print(f"    Pearson r  = {corr_a_p:+.4f}")
print(f"    Spearman ρ = {corr_a_s:+.4f}")
print(f"\n  v2 Scheme B (Leading-Heavy):")
print(f"    Pearson r  = {corr_b_p:+.4f}")
print(f"    Spearman ρ = {corr_b_s:+.4f}")

# Direction of improvement
for name, cp, cs in [("A", corr_a_p, corr_a_s), ("B", corr_b_p, corr_b_s)]:
    imp_p = abs(cp) - 0.008
    print(f"\n  Scheme {name} improvement: Pearson |r| delta = {imp_p:+.4f}, Spearman delta = {abs(cs) - 0.0074:+.4f}")

print("\nDone.")
