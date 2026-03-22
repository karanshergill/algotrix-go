"""Build E5 labels — E3 + churning demotion filter.

E5 keeps E3's 3-class output but demotes false trends back to Range
when the day looks like churning: weak return + high volume + concentrated flow.
"""

import numpy as np
import pandas as pd
import psycopg2
from pathlib import Path


def get_conn():
    return psycopg2.connect(host="localhost", user="me", password="algotrix", dbname="atdb")


def load_data():
    """Load E3 labels + volume/concentration metrics."""
    conn = get_conn()

    # E3 labels + return from regime_ground_truth
    gt = pd.read_sql("""
        SELECT date, coincident_label as label_e3, nifty_return
        FROM regime_ground_truth
        ORDER BY date
    """, conn)
    gt["date"] = pd.to_datetime(gt["date"]).dt.date

    # Market turnover for volume_ratio
    vol = pd.read_sql("""
        SELECT date, SUM(traded_value) as market_turnover
        FROM nse_cm_bhavcopy GROUP BY date ORDER BY date
    """, conn)
    vol["date"] = pd.to_datetime(vol["date"]).dt.date
    vol["turnover_20d_avg"] = vol["market_turnover"].rolling(20, min_periods=5).mean()
    vol["volume_ratio"] = vol["market_turnover"] / vol["turnover_20d_avg"]

    # Top-10 turnover concentration
    conc = pd.read_sql("""
        WITH ranked AS (
            SELECT date, traded_value,
                ROW_NUMBER() OVER (PARTITION BY date ORDER BY traded_value DESC) as rn,
                SUM(traded_value) OVER (PARTITION BY date) as total_tv
            FROM nse_cm_bhavcopy
        )
        SELECT date,
            SUM(CASE WHEN rn <= 10 THEN traded_value ELSE 0 END) / NULLIF(MAX(total_tv), 0) as top10_share
        FROM ranked
        GROUP BY date ORDER BY date
    """, conn)
    conc["date"] = pd.to_datetime(conc["date"]).dt.date
    conc["conc_20d_avg"] = conc["top10_share"].rolling(20, min_periods=5).mean()
    conc["concentration_ratio"] = conc["top10_share"] / conc["conc_20d_avg"]

    conn.close()

    # Merge
    df = gt.merge(vol[["date", "volume_ratio"]], on="date", how="left")
    df = df.merge(conc[["date", "concentration_ratio"]], on="date", how="left")
    return df


def apply_churning_filter(df):
    """Apply E5 churning demotion: weak-return trends with high vol+conc → Range."""

    # Rolling 252-day P40 of abs(nifty_return)
    abs_ret = df["nifty_return"].abs()
    ret_p40 = abs_ret.rolling(252, min_periods=63).quantile(0.40)

    is_trend = df["label_e3"].isin(["Trend-Up", "Trend-Down"])
    weak_move = abs_ret < ret_p40
    high_volume = df["volume_ratio"] > 1.10
    high_concentration = df["concentration_ratio"] > 1.05

    churning = is_trend & weak_move & high_volume & high_concentration

    df["label_e5"] = df["label_e3"].copy()
    df.loc[churning, "label_e5"] = "Range"
    df["was_demoted"] = churning.astype(int)

    return df


def main():
    print("=" * 70)
    print("  BUILD E5 LABELS — E3 + Churning Demotion")
    print("=" * 70)

    df = load_data()
    print(f"Loaded {len(df)} days")
    print(f"Date range: {df['date'].min()} → {df['date'].max()}")
    print(f"Volume ratio NaN: {df['volume_ratio'].isna().sum()}")
    print(f"Concentration ratio NaN: {df['concentration_ratio'].isna().sum()}")

    df = apply_churning_filter(df)

    # Report
    demoted = df["was_demoted"].sum()
    total = len(df)
    print(f"\n--- Demotion Summary ---")
    print(f"Total days: {total}")
    print(f"Demoted (trend → Range): {demoted} ({demoted/total*100:.1f}%)")

    print(f"\n--- E3 Class Distribution ---")
    e3_dist = df["label_e3"].value_counts()
    for label, count in e3_dist.items():
        print(f"  {label:<12} {count:>5} ({count/total*100:.1f}%)")

    print(f"\n--- E5 Class Distribution ---")
    e5_dist = df["label_e5"].value_counts()
    for label, count in e5_dist.items():
        print(f"  {label:<12} {count:>5} ({count/total*100:.1f}%)")

    # Demotion breakdown by original label
    demoted_rows = df[df["was_demoted"] == 1]
    if len(demoted_rows) > 0:
        print(f"\n--- Demoted Days Breakdown ---")
        for label in ["Trend-Up", "Trend-Down"]:
            n = (demoted_rows["label_e3"] == label).sum()
            print(f"  {label} → Range: {n}")
        print(f"\n  Demoted days avg volume_ratio: {demoted_rows['volume_ratio'].mean():.3f}")
        print(f"  Demoted days avg concentration_ratio: {demoted_rows['concentration_ratio'].mean():.3f}")
        print(f"  Demoted days avg abs(return): {demoted_rows['nifty_return'].abs().mean()*100:.3f}%")

    # Save CSV
    out_cols = ["date", "label_e3", "label_e5", "was_demoted", "volume_ratio", "concentration_ratio"]
    out_path = Path(__file__).resolve().parent.parent / "data" / "e5_labels.csv"
    df[out_cols].to_csv(out_path, index=False)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
