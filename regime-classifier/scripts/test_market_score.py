#!/usr/bin/env python3 -u
"""
Disposable prototype: daily market score (-100..+100) for NSE.
Validates whether the score predicts next-day Nifty returns.
"""

import sys
import numpy as np
import pandas as pd
import psycopg2
import talib

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

# Trend features
df["ema20"] = talib.EMA(df["close"].values, timeperiod=20)
df["close_vs_ema"] = (df["close"] - df["ema20"]) / df["ema20"] * 100  # % above/below
df["ema20_slope"] = df["ema20"].pct_change(5) * 100  # 5-day slope %
df["adx14"] = talib.ADX(df["high"].values, df["low"].values, df["close"].values, timeperiod=14)

# Participation features
df = df.join(ad[["advances", "declines"]])
df["ad_ratio"] = df["advances"] / df["declines"].replace(0, np.nan)
df = df.join(breadth)

# Sentiment features
df = df.join(vix[["vix_close"]])
df["vix_roc"] = df["vix_close"] / df["vix_close"].rolling(5).mean() - 1  # vs 5d avg

# ── 4. Z-score normalization (60-day rolling) ────────────────────────────────
print("Z-scoring features …")
FEATURES = {
    # name: (column, weight, invert?)
    # invert=True means higher raw = bearish (e.g. VIX)
    "close_vs_ema": ("close_vs_ema", 0.15, False),
    "ema20_slope":  ("ema20_slope",  0.10, False),
    "adx14":        ("adx14",        0.15, False),   # high ADX in uptrend = bullish (adjusted below)
    "ad_ratio":     ("ad_ratio",     0.15, False),
    "pct_above_ema20": ("pct_above_ema20", 0.15, False),
    "vix_close":    ("vix_close",    0.15, True),
    "vix_roc":      ("vix_roc",      0.15, True),
}

for name, (col, weight, invert) in FEATURES.items():
    roll_mean = df[col].rolling(60, min_periods=40).mean()
    roll_std  = df[col].rolling(60, min_periods=40).std()
    z = (df[col] - roll_mean) / roll_std.replace(0, np.nan)
    if invert:
        z = -z
    df[f"z_{name}"] = z

# ADX sign: ADX measures trend *strength* not direction.
# Multiply ADX z-score by sign of close_vs_ema to give it direction.
df["z_adx14"] = df["z_adx14"] * np.sign(df["z_close_vs_ema"])

# ── 5. Composite score ───────────────────────────────────────────────────────
print("Computing composite score …")
z_cols = [f"z_{name}" for name in FEATURES]
weights = np.array([v[1] for v in FEATURES.values()])
weights = weights / weights.sum()  # normalise to 1

z_matrix = df[z_cols].values
df["raw_score"] = np.nansum(z_matrix * weights, axis=1)
# NaN where any critical feature is NaN
df.loc[df[z_cols].isna().any(axis=1), "raw_score"] = np.nan

# Percentile rank vs trailing 252 days → 0..100
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

df["market_score"] = rolling_percentile_rank(df["raw_score"])

# ── 6. Volatility score (separate dimension) ─────────────────────────────────
df["volatility_score"] = rolling_percentile_rank(df["z_vix_close"].mul(-1))  # un-invert: high VIX = high vol score

# ── 7. Labels, deltas, next-day return ────────────────────────────────────────
df["regime_label"] = pd.cut(
    df["market_score"],
    bins=[-0.01, 35, 65, 100.01],
    labels=["Bearish", "Neutral", "Bullish"],
)
df["score_delta_1d"] = df["market_score"].diff()
df["next_day_nifty_return"] = df["close"].pct_change().shift(-1) * 100  # in %

# Component z-score groups for output
df["trend_z"] = (df["z_close_vs_ema"] * 0.15 + df["z_ema20_slope"] * 0.10 + df["z_adx14"] * 0.15) / 0.40
df["participation_z"] = (df["z_ad_ratio"] * 0.15 + df["z_pct_above_ema20"] * 0.15) / 0.30
df["sentiment_z"] = (df["z_vix_close"] * 0.15 + df["z_vix_roc"] * 0.15) / 0.30

# ── 8. Trim warmup and NaN rows ──────────────────────────────────────────────
out = df[["market_score", "volatility_score", "regime_label", "score_delta_1d",
          "trend_z", "participation_z", "sentiment_z", "next_day_nifty_return"]].copy()
out = out.dropna(subset=["market_score"])
out.index.name = "date"

# ── 9. Save CSV ──────────────────────────────────────────────────────────────
csv_path = "/home/me/projects/algotrix-go/regime-classifier/scripts/market_score_results.csv"
out.to_csv(csv_path)
print(f"\nSaved {len(out)} rows to {csv_path}")

# ── 10. Validation output ────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("VALIDATION RESULTS")
print("=" * 60)

print(f"\nTotal scored trading days: {len(out)}")
print(f"Date range: {out.index.min().date()} to {out.index.max().date()}")

print("\n── Regime counts ──")
counts = out["regime_label"].value_counts()
for label in ["Bullish", "Neutral", "Bearish"]:
    n = counts.get(label, 0)
    print(f"  {label:>8s}: {n:>5d} days ({n/len(out)*100:.1f}%)")

print("\n── Avg next-day Nifty return by regime ──")
regime_returns = out.groupby("regime_label", observed=True)["next_day_nifty_return"].agg(["mean", "median", "std", "count"])
for label in ["Bullish", "Neutral", "Bearish"]:
    if label in regime_returns.index:
        r = regime_returns.loc[label]
        print(f"  {label:>8s}: mean={r['mean']:+.4f}%  median={r['median']:+.4f}%  std={r['std']:.4f}%  n={int(r['count'])}")

print("\n── Score ↔ next-day return correlation ──")
valid = out.dropna(subset=["next_day_nifty_return"])
corr = valid["market_score"].corr(valid["next_day_nifty_return"])
print(f"  Pearson r = {corr:.4f}")

# Rank correlation (more robust)
from scipy.stats import spearmanr
rho, pval = spearmanr(valid["market_score"], valid["next_day_nifty_return"])
print(f"  Spearman ρ = {rho:.4f}  (p={pval:.2e})")

print("\n── Score distribution ──")
print(f"  mean={out['market_score'].mean():.1f}  std={out['market_score'].std():.1f}  "
      f"min={out['market_score'].min():.1f}  max={out['market_score'].max():.1f}")

print("\n── Volatility score distribution ──")
print(f"  mean={out['volatility_score'].mean():.1f}  std={out['volatility_score'].std():.1f}  "
      f"min={out['volatility_score'].min():.1f}  max={out['volatility_score'].max():.1f}")

# Quintile analysis
print("\n── Quintile analysis (market_score → next-day return) ──")
valid["quintile"] = pd.qcut(valid["market_score"], 5, labels=["Q1(bear)", "Q2", "Q3", "Q4", "Q5(bull)"])
q_returns = valid.groupby("quintile", observed=True)["next_day_nifty_return"].agg(["mean", "count"])
for q in q_returns.index:
    print(f"  {q}: mean={q_returns.loc[q, 'mean']:+.4f}%  n={int(q_returns.loc[q, 'count'])}")

print("\nDone.")
