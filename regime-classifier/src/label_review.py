"""Label Review Experiment — Compare 3 candidate label sets.

Computes Set A (current), Set B (4-class session character), Set C (3-class routing)
for all trading days and evaluates: distribution, economic separation, stability.
"""

import psycopg2
import numpy as np
import pandas as pd
from collections import Counter

DB_DSN = "host=localhost dbname=atdb user=me password=algotrix"


def load_data():
    """Load Nifty 50 OHLCV, breadth, VIX, and current ground truth."""
    conn = psycopg2.connect(DB_DSN)
    
    # Nifty 50 daily OHLCV
    nifty = pd.read_sql("""
        SELECT date, open, high, low, close, volume
        FROM nse_indices_daily
        WHERE index = 'Nifty 50'
        ORDER BY date
    """, conn, parse_dates=["date"])
    
    # VIX
    vix = pd.read_sql("""
        SELECT date, open as vix_open, high as vix_high, low as vix_low, 
               close as vix_close
        FROM nse_vix_daily
        ORDER BY date
    """, conn, parse_dates=["date"])
    
    # Breadth: advances / (advances + declines) from bhavcopy
    breadth = pd.read_sql("""
        SELECT date,
               SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END)::float /
               NULLIF(SUM(CASE WHEN close != prev_close THEN 1 ELSE 0 END), 0) as breadth_ratio,
               COUNT(*) as total_stocks
        FROM nse_cm_bhavcopy
        GROUP BY date
        ORDER BY date
    """, conn, parse_dates=["date"])
    
    # Current ground truth
    gt = pd.read_sql("""
        SELECT date, nifty_return, breadth_ratio as gt_breadth, 
               vix_change_pct, coincident_label
        FROM regime_ground_truth
        ORDER BY date
    """, conn, parse_dates=["date"])
    
    conn.close()
    
    # Merge
    df = nifty.merge(vix, on="date", how="left")
    df = df.merge(breadth, on="date", how="left")
    df = df.merge(gt[["date", "coincident_label"]], on="date", how="left")
    
    # Compute derived features
    df = df.sort_values("date").reset_index(drop=True)
    
    # Returns
    df["prev_close"] = df["close"].shift(1)
    df["return_pct"] = (df["close"] / df["prev_close"]) - 1
    
    # Day range
    df["day_range"] = df["high"] - df["low"]
    df["day_range_pct"] = df["day_range"] / df["prev_close"]
    
    # Close location in range: 0 = closed at low, 1 = closed at high
    df["close_in_range"] = (df["close"] - df["low"]) / df["day_range"].replace(0, np.nan)
    
    # Rolling 20-day average range
    df["avg_range_20d"] = df["day_range"].rolling(20).mean()
    df["range_ratio"] = df["day_range"] / df["avg_range_20d"].replace(0, np.nan)
    
    # Rolling 20-day median range_pct
    df["median_range_pct_20d"] = df["day_range_pct"].rolling(20).median()
    df["range_pct_vs_median"] = df["day_range_pct"] / df["median_range_pct_20d"].replace(0, np.nan)
    
    # VIX change
    df["vix_prev"] = df["vix_close"].shift(1)
    df["vix_change_pct"] = ((df["vix_close"] - df["vix_prev"]) / df["vix_prev"]) * 100
    
    # Drop first 20 rows (need rolling window)
    df = df.iloc[20:].reset_index(drop=True)
    
    return df


def label_set_a(row):
    """Current: Bull / Neutral / Bear (from ground_truth.py logic)."""
    bullish = 0
    bearish = 0
    
    ret = row["return_pct"]
    breadth = row["breadth_ratio"]
    vix_chg = row["vix_change_pct"]
    
    if pd.isna(ret) or pd.isna(breadth) or pd.isna(vix_chg):
        return "Unknown"
    
    if ret > 0.003: bullish += 1
    if ret < -0.003: bearish += 1
    if breadth > 0.55: bullish += 1
    if breadth < 0.45: bearish += 1
    if vix_chg < -7.5: bullish += 1
    if vix_chg > 7.5: bearish += 1
    
    if bullish >= 2: return "Bullish"
    if bearish >= 2: return "Bearish"
    return "Neutral"


def label_set_b(row):
    """4-class session character: Trend Up / Trend Down / Range-Chop / Volatile-Whipsaw."""
    ret = row["return_pct"]
    cir = row["close_in_range"]  # close-in-range [0,1]
    rr = row["range_ratio"]      # range vs 20d avg
    breadth = row["breadth_ratio"]
    
    if pd.isna(ret) or pd.isna(cir) or pd.isna(rr):
        return "Unknown"
    
    abs_ret = abs(ret)
    
    # Volatile/Whipsaw: wide range but close near middle (reversal-like)
    # High range + close in middle 40% of range
    if rr > 1.5 and 0.30 <= cir <= 0.70:
        return "Volatile-Whipsaw"
    
    # Trend Up: positive return, close near high, above-average range
    if ret > 0.005 and cir > 0.70 and rr > 0.8:
        return "Trend-Up"
    
    # Trend Down: negative return, close near low, above-average range  
    if ret < -0.005 and cir < 0.30 and rr > 0.8:
        return "Trend-Down"
    
    # Range/Chop: small return, close mid-range, modest range
    if abs_ret < 0.005 and rr < 1.3:
        return "Range-Chop"
    
    # Fallback: use return direction with weaker criteria
    if ret > 0.003 and cir > 0.50:
        return "Trend-Up"
    if ret < -0.003 and cir < 0.50:
        return "Trend-Down"
    
    # Remaining: default to Range-Chop
    return "Range-Chop"


def label_set_c(row):
    """3-class intraday routing: Directional / Range / Chaotic."""
    ret = row["return_pct"]
    cir = row["close_in_range"]
    rr = row["range_ratio"]
    breadth = row["breadth_ratio"]
    
    if pd.isna(ret) or pd.isna(cir) or pd.isna(rr):
        return "Unknown"
    
    abs_ret = abs(ret)
    
    # Chaotic: wide range + close near middle (didn't pick direction)
    # OR wide range + return/breadth mismatch
    if rr > 1.4 and 0.25 <= cir <= 0.75 and abs_ret < 0.005:
        return "Chaotic"
    if rr > 1.5 and 0.30 <= cir <= 0.70:
        return "Chaotic"
    
    # Directional: meaningful return + close near extreme + breadth confirms
    if abs_ret > 0.005 and (cir > 0.70 or cir < 0.30):
        return "Directional"
    # Slightly weaker: decent return + close somewhat extreme
    if abs_ret > 0.003 and (cir > 0.60 or cir < 0.40):
        return "Directional"
    
    # Range: everything else (small return, modest range, close mid-range)
    return "Range"


def evaluate_label_set(df, label_col, set_name):
    """Compute evaluation metrics for a label set."""
    labels = df[label_col]
    valid = labels != "Unknown"
    df_valid = df[valid].copy()
    labels_valid = labels[valid]
    
    print(f"\n{'='*70}")
    print(f"  LABEL SET: {set_name}")
    print(f"{'='*70}")
    
    # 1. Distribution
    counts = Counter(labels_valid)
    total = len(labels_valid)
    print(f"\n📊 Distribution ({total} trading days):")
    for label, count in sorted(counts.items(), key=lambda x: -x[1]):
        pct = count / total * 100
        bar = "█" * int(pct / 2)
        print(f"  {label:20s}  {count:5d}  ({pct:5.1f}%)  {bar}")
    
    # Balance check
    max_pct = max(c / total for c in counts.values()) * 100
    min_pct = min(c / total for c in counts.values()) * 100
    print(f"\n  Balance: max={max_pct:.1f}%, min={min_pct:.1f}%, ratio={max_pct/max(min_pct,0.1):.1f}x")
    
    # 2. Economic separation
    print(f"\n📈 Economic Separation (per label):")
    print(f"  {'Label':20s} {'Avg Ret':>8s} {'Med Ret':>8s} {'Std Ret':>8s} {'Avg Range%':>10s} {'Avg CIR':>8s} {'Avg Breadth':>12s}")
    print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*8} {'-'*10} {'-'*8} {'-'*12}")
    
    for label in sorted(counts.keys()):
        mask = labels_valid == label
        subset = df_valid[mask]
        avg_ret = subset["return_pct"].mean() * 100
        med_ret = subset["return_pct"].median() * 100
        std_ret = subset["return_pct"].std() * 100
        avg_range = subset["day_range_pct"].mean() * 100
        avg_cir = subset["close_in_range"].mean()
        avg_breadth = subset["breadth_ratio"].mean()
        print(f"  {label:20s} {avg_ret:+8.3f} {med_ret:+8.3f} {std_ret:8.3f} {avg_range:10.3f} {avg_cir:8.3f} {avg_breadth:12.3f}")
    
    # 3. Year-over-year stability
    print(f"\n📅 Year-over-Year Stability:")
    df_valid_copy = df_valid.copy()
    df_valid_copy["year"] = df_valid_copy["date"].dt.year
    df_valid_copy["_label"] = labels_valid.values
    
    years = sorted(df_valid_copy["year"].unique())
    all_labels = sorted(counts.keys())
    
    print(f"  {'Year':>6s}", end="")
    for label in all_labels:
        print(f"  {label[:12]:>12s}", end="")
    print()
    
    for year in years:
        year_data = df_valid_copy[df_valid_copy["year"] == year]
        year_counts = Counter(year_data["_label"])
        year_total = len(year_data)
        print(f"  {year:>6d}", end="")
        for label in all_labels:
            pct = year_counts.get(label, 0) / year_total * 100
            print(f"  {pct:11.1f}%", end="")
        print()
    
    return counts


def label_set_d(row):
    """4-class revised: Trend-Up / Trend-Down / Range / Expansion."""
    ret = row["return_pct"]
    cir = row["close_in_range"]
    rr = row["range_ratio"]
    breadth = row["breadth_ratio"]
    
    if pd.isna(ret) or pd.isna(cir) or pd.isna(rr):
        return "Unknown"
    
    abs_ret = abs(ret)
    
    # Expansion: wide range but close near middle (no conviction despite big move)
    if rr > 1.3 and 0.25 <= cir <= 0.75:
        return "Expansion"
    
    # Trend-Up: positive return, close near high, breadth confirms
    if ret > 0.003 and cir > 0.60 and (pd.isna(breadth) or breadth > 0.50):
        return "Trend-Up"
    
    # Trend-Down: negative return, close near low, breadth confirms
    if ret < -0.003 and cir < 0.40 and (pd.isna(breadth) or breadth < 0.50):
        return "Trend-Down"
    
    # Range: small return, modest range
    if abs_ret < 0.005 and rr < 1.2:
        return "Range"
    
    # Fallback: use weaker directional signal
    if ret > 0.002 and cir > 0.50:
        return "Trend-Up"
    if ret < -0.002 and cir < 0.50:
        return "Trend-Down"
    
    return "Range"


def main():
    print("Loading data from atdb...")
    df = load_data()
    print(f"Loaded {len(df)} trading days ({df['date'].min()} to {df['date'].max()})")
    
    # Compute all label sets
    print("\nComputing label sets...")
    df["label_a"] = df.apply(label_set_a, axis=1)
    df["label_b"] = df.apply(label_set_b, axis=1)
    df["label_c"] = df.apply(label_set_c, axis=1)
    df["label_d"] = df.apply(label_set_d, axis=1)
    
    # Cross-check Set A vs existing ground truth
    has_gt = df["coincident_label"].notna()
    if has_gt.sum() > 0:
        match = (df.loc[has_gt, "label_a"] == df.loc[has_gt, "coincident_label"]).mean()
        print(f"\nSet A vs existing ground_truth match: {match:.1%}")
    
    # Evaluate each
    evaluate_label_set(df, "label_a", "A — Current (Bull/Neutral/Bear)")
    evaluate_label_set(df, "label_b", "B — Session Character (4-class)")
    evaluate_label_set(df, "label_c", "C — Intraday Routing (3-class)")
    evaluate_label_set(df, "label_d", "D — Directional + Session Character (4-class revised)")
    
    # Cross-tabulation: A vs D
    print(f"\n{'='*70}")
    print(f"  CROSS-TABULATION: Set A vs Set D")
    print(f"{'='*70}")
    ct_ad = pd.crosstab(df["label_a"], df["label_d"], margins=True)
    print(ct_ad.to_string())
    
    # Cross-tabulation: A vs B
    print(f"\n{'='*70}")
    print(f"  CROSS-TABULATION: Set A vs Set B")
    print(f"{'='*70}")
    ct_ab = pd.crosstab(df["label_a"], df["label_b"], margins=True)
    print(ct_ab.to_string())
    
    # Cross-tabulation: A vs C
    print(f"\n{'='*70}")
    print(f"  CROSS-TABULATION: Set A vs Set C")
    print(f"{'='*70}")
    ct_ac = pd.crosstab(df["label_a"], df["label_c"], margins=True)
    print(ct_ac.to_string())
    
    # Save for further analysis
    output_path = "/home/me/projects/algotrix-go/regime-classifier/label_review_results.csv"
    df[["date", "return_pct", "day_range_pct", "close_in_range", "range_ratio",
        "breadth_ratio", "vix_change_pct", "label_a", "label_b", "label_c", "label_d"]].to_csv(output_path, index=False)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
