"""E1/E2/E3 Label Calibration Bake-off.

Compares 3 labelling variants on the same universe (2020-01-02 → 2026-03-20).
All data from raw DB tables, NOT from regime_ground_truth.
"""

import numpy as np
import pandas as pd
import psycopg2
import os
import sys

DB_DSN = "host=localhost dbname=atdb user=me password=algotrix"
DATE_START = "2020-01-02"
DATE_END = "2026-03-20"
CSV_OUT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "calibration_labels.csv")

np.random.seed(42)


# ── Data Loading ─────────────────────────────────────────────────────────────

def load_data():
    conn = psycopg2.connect(DB_DSN)

    nifty = pd.read_sql(
        """SELECT date, open, high, low, close, volume, turnover
           FROM nse_indices_daily WHERE index = 'Nifty 50'
           AND date BETWEEN %s AND %s ORDER BY date""",
        conn, params=(DATE_START, DATE_END), parse_dates=["date"],
    )

    vix = pd.read_sql(
        """SELECT date, open, high, low, close
           FROM nse_vix_daily
           WHERE date BETWEEN %s AND %s ORDER BY date""",
        conn, params=(DATE_START, DATE_END), parse_dates=["date"],
    )

    breadth = pd.read_sql(
        """SELECT date,
           SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END)::float /
           NULLIF(SUM(CASE WHEN close != prev_close THEN 1 ELSE 0 END), 0) as breadth_ratio
           FROM nse_cm_bhavcopy
           WHERE date BETWEEN %s AND %s
           GROUP BY date ORDER BY date""",
        conn, params=(DATE_START, DATE_END), parse_dates=["date"],
    )

    conn.close()
    return nifty, vix, breadth


# ── Derived Features ─────────────────────────────────────────────────────────

def compute_features(nifty, vix, breadth):
    df = nifty.sort_values("date").reset_index(drop=True)

    # Return
    df["prev_close"] = df["close"].shift(1)
    df["return_pct"] = (df["close"] / df["prev_close"]) - 1

    # CIR
    day_range = df["high"] - df["low"]
    df["cir"] = np.where(day_range == 0, 0.5, (df["close"] - df["low"]) / day_range)

    # Range pct
    df["range_pct"] = day_range / df["close"] * 100

    # Range ratio
    avg_range_20d = day_range.rolling(20).mean()
    df["range_ratio"] = np.where(avg_range_20d == 0, 1.0, day_range / avg_range_20d)

    # Volume z-score (rolling 20-day median)
    vol_median = df["volume"].rolling(20).median()
    vol_std = df["volume"].rolling(20).std()
    df["vol_zscore"] = np.where(vol_std == 0, 0.0, (df["volume"] - vol_median) / vol_std)

    # Breadth
    df = df.merge(breadth, on="date", how="left")

    # VIX
    vix = vix.sort_values("date").reset_index(drop=True)
    vix_df = vix[["date", "close"]].rename(columns={"close": "vix_level"})
    vix_df["vix_change_pct"] = (vix_df["vix_level"] / vix_df["vix_level"].shift(1) - 1) * 100
    df = df.merge(vix_df, on="date", how="left")

    # Gap pct (for gap-sign baseline)
    df["gap_pct"] = (df["open"] / df["prev_close"]) - 1

    return df


# ── Labelling Variants ───────────────────────────────────────────────────────

def label_e1(row):
    ret = row["return_pct"]
    cir = row["cir"]
    breadth = row["breadth_ratio"]

    if pd.isna(ret) or pd.isna(cir):
        return None

    # Strong trend
    if ret > 0.003 and cir > 0.60 and (pd.isna(breadth) or breadth > 0.50):
        return "Trend-Up"
    if ret < -0.003 and cir < 0.40 and (pd.isna(breadth) or breadth < 0.50):
        return "Trend-Down"

    # Weaker directional
    if ret > 0.002 and cir > 0.50:
        return "Trend-Up"
    if ret < -0.002 and cir < 0.50:
        return "Trend-Down"

    return "Range"


def label_e2(row):
    """E1 + energy confirmation: trend requires range_ratio > 0.8 OR vol_zscore > -0.5."""
    e1 = label_e1(row)
    if e1 in ("Trend-Up", "Trend-Down"):
        rr = row["range_ratio"]
        vz = row["vol_zscore"]
        has_energy = (not pd.isna(rr) and rr > 0.8) or (not pd.isna(vz) and vz > -0.5)
        if not has_energy:
            return "Range"
    return e1


def label_e3(row):
    """Percentile-based thresholds using pre-computed rolling percentiles."""
    ret = row["return_pct"]
    cir = row["cir"]
    breadth = row["breadth_ratio"]

    if pd.isna(ret) or pd.isna(cir):
        return None

    ret_p67 = row["ret_p67"]
    cir_p33 = row["cir_p33"]
    cir_p67 = row["cir_p67"]
    breadth_p33 = row["breadth_p33"]
    breadth_p67 = row["breadth_p67"]

    # Strong trend with P67 thresholds
    if ret > ret_p67 and cir > cir_p67 and (pd.isna(breadth) or breadth > breadth_p67):
        return "Trend-Up"
    if ret < -ret_p67 and cir < cir_p33 and (pd.isna(breadth) or breadth < breadth_p33):
        return "Trend-Down"

    # Weaker directional (use midpoint between P33 and P67)
    ret_p33 = row["ret_p33"]
    cir_mid = (cir_p33 + cir_p67) / 2
    if ret > ret_p33 and cir > cir_mid:
        return "Trend-Up"
    if ret < -ret_p33 and cir < cir_mid:
        return "Trend-Down"

    return "Range"


def compute_e3_percentiles(df):
    """Rolling 252-day percentiles for E3 (expanding window for < 252 days)."""
    n = len(df)
    abs_ret = df["return_pct"].abs()

    ret_p33 = np.full(n, np.nan)
    ret_p67 = np.full(n, np.nan)
    cir_p33 = np.full(n, np.nan)
    cir_p67 = np.full(n, np.nan)
    breadth_p33 = np.full(n, np.nan)
    breadth_p67 = np.full(n, np.nan)

    for i in range(1, n):
        start = max(0, i - 252)
        window = slice(start, i)

        ret_vals = abs_ret.iloc[window].dropna()
        if len(ret_vals) > 0:
            ret_p33[i] = np.percentile(ret_vals, 33)
            ret_p67[i] = np.percentile(ret_vals, 67)

        cir_vals = df["cir"].iloc[window].dropna()
        if len(cir_vals) > 0:
            cir_p33[i] = np.percentile(cir_vals, 33)
            cir_p67[i] = np.percentile(cir_vals, 67)

        br_vals = df["breadth_ratio"].iloc[window].dropna()
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


# ── Report Helpers ────────────────────────────────────────────────────────────

def print_class_distribution(labels, name):
    counts = labels.value_counts()
    total = len(labels.dropna())
    print(f"\n  Class distribution ({name}):")
    for label in ["Trend-Up", "Range", "Trend-Down"]:
        c = counts.get(label, 0)
        print(f"    {label:>12s}: {c:5d}  ({c/total*100:5.1f}%)")


def compute_baselines(df, label_col):
    valid = df.dropna(subset=[label_col]).copy()
    labels = valid[label_col].values
    n = len(labels)

    # Majority class
    majority = pd.Series(labels).mode()[0]
    majority_acc = np.mean(labels == majority)

    # Persistence (yesterday's label)
    persist_pred = np.roll(labels, 1)
    persist_pred[0] = majority
    persist_acc = np.mean(labels[1:] == persist_pred[1:])

    # Gap-sign rule
    gaps = valid["gap_pct"].values
    gap_pred = np.where(gaps > 0, "Trend-Up", np.where(gaps < 0, "Trend-Down", "Range"))
    gap_acc = np.mean(labels == gap_pred)

    # Previous-day regime (same as persistence but explicit)
    prev_regime_acc = persist_acc  # identical by definition

    return {
        f"Majority ({majority})": majority_acc,
        "Persistence": persist_acc,
        "Gap-sign": gap_acc,
        "Previous-day regime": prev_regime_acc,
    }


def print_transition_matrix(labels, name):
    valid = labels.dropna()
    transitions = pd.crosstab(
        valid.shift(1).dropna(), valid.iloc[1:],
        rownames=["From"], colnames=["To"], normalize="index",
    )
    print(f"\n  Transition matrix ({name}):")
    print(transitions.round(3).to_string(float_format=lambda x: f"{x:.3f}").replace("\n", "\n  "))


def print_economic_validation(df, label_col, name):
    valid = df.dropna(subset=[label_col])
    print(f"\n  Economic validation ({name}):")
    print(f"    {'Label':>12s} {'Mean Ret%':>10s} {'Mean Range%':>12s} {'Mean Volume':>14s}")
    print(f"    {'-'*12} {'-'*10} {'-'*12} {'-'*14}")
    for label in ["Trend-Up", "Range", "Trend-Down"]:
        subset = valid[valid[label_col] == label]
        if len(subset) == 0:
            continue
        mr = subset["return_pct"].mean() * 100
        mrng = subset["range_pct"].mean()
        mv = subset["volume"].mean()
        print(f"    {label:>12s} {mr:>+10.4f} {mrng:>12.3f} {mv:>14,.0f}")


def print_label_agreement(df):
    valid = df.dropna(subset=["label_e1", "label_e2", "label_e3"])
    n = len(valid)
    e1_e2 = (valid["label_e1"] == valid["label_e2"]).sum()
    e1_e3 = (valid["label_e1"] == valid["label_e3"]).sum()
    e2_e3 = (valid["label_e2"] == valid["label_e3"]).sum()
    all3 = ((valid["label_e1"] == valid["label_e2"]) & (valid["label_e2"] == valid["label_e3"])).sum()

    print(f"\n{'='*70}")
    print(f"  LABEL AGREEMENT")
    print(f"{'='*70}")
    print(f"  E1 == E2: {e1_e2:5d} / {n} ({e1_e2/n*100:.1f}%)")
    print(f"  E1 == E3: {e1_e3:5d} / {n} ({e1_e3/n*100:.1f}%)")
    print(f"  E2 == E3: {e2_e3:5d} / {n} ({e2_e3/n*100:.1f}%)")
    print(f"  All agree: {all3:5d} / {n} ({all3/n*100:.1f}%)")


def print_spot_checks(df, n=10):
    """Print n random days where E1 and E2 disagree."""
    disagree = df[df["label_e1"] != df["label_e2"]].dropna(subset=["label_e1", "label_e2"])
    if len(disagree) == 0:
        print("\n  No E1/E2 disagreements found.")
        return

    sample = disagree.sample(min(n, len(disagree)), random_state=42)
    print(f"\n{'='*70}")
    print(f"  SPOT-CHECK: {len(sample)} random E1 ≠ E2 days")
    print(f"{'='*70}")

    cols = ["date", "return_pct", "cir", "breadth_ratio", "range_pct",
            "range_ratio", "vol_zscore", "vix_level", "vix_change_pct",
            "label_e1", "label_e2", "label_e3"]
    for _, row in sample.sort_values("date").iterrows():
        print(f"\n  {row['date'].strftime('%Y-%m-%d')}:")
        print(f"    Return: {row['return_pct']*100:+.3f}%  CIR: {row['cir']:.3f}  "
              f"Breadth: {row['breadth_ratio']:.3f}" if not pd.isna(row['breadth_ratio'])
              else f"    Return: {row['return_pct']*100:+.3f}%  CIR: {row['cir']:.3f}  Breadth: NaN")
        print(f"    Range%: {row['range_pct']:.3f}  RangeRatio: {row['range_ratio']:.3f}  "
              f"VolZ: {row['vol_zscore']:+.3f}")
        print(f"    VIX: {row['vix_level']:.2f}  VIX Δ: {row['vix_change_pct']:+.2f}%"
              if not pd.isna(row['vix_level']) else "    VIX: NaN")
        print(f"    E1: {row['label_e1']}  |  E2: {row['label_e2']}  |  E3: {row['label_e3']}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Loading data from DB...")
    nifty, vix, breadth = load_data()
    print(f"  Nifty rows: {len(nifty)}  VIX rows: {len(vix)}  Breadth rows: {len(breadth)}")

    print("Computing features...")
    df = compute_features(nifty, vix, breadth)

    print("Computing E3 rolling percentiles...")
    df = compute_e3_percentiles(df)

    print("Applying labels...")
    df["label_e1"] = df.apply(label_e1, axis=1)
    df["label_e2"] = df.apply(label_e2, axis=1)
    df["label_e3"] = df.apply(label_e3, axis=1)

    # Save CSV
    csv_cols = ["date", "return_pct", "cir", "breadth_ratio", "range_pct",
                "range_ratio", "vol_zscore", "vix_level", "vix_change_pct",
                "label_e1", "label_e2", "label_e3"]
    os.makedirs(os.path.dirname(CSV_OUT), exist_ok=True)
    df[csv_cols].to_csv(CSV_OUT, index=False)
    print(f"\nCSV saved: {CSV_OUT} ({len(df)} rows)")

    # ── Per-variant reports ──────────────────────────────────────────────
    for variant, col in [("E1", "label_e1"), ("E2", "label_e2"), ("E3", "label_e3")]:
        print(f"\n{'='*70}")
        print(f"  VARIANT {variant}")
        print(f"{'='*70}")

        print_class_distribution(df[col], variant)

        baselines = compute_baselines(df, col)
        print(f"\n  Baselines ({variant}):")
        for bname, bacc in baselines.items():
            print(f"    {bname:<25s}: {bacc:.4f} ({bacc*100:.1f}%)")

        print_transition_matrix(df[col], variant)
        print_economic_validation(df, col, variant)

    # ── Cross-variant analysis ───────────────────────────────────────────
    print_label_agreement(df)
    print_spot_checks(df, n=10)

    print(f"\n{'='*70}")
    print("  DONE")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
