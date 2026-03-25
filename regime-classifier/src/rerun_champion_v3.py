"""Rerun champion model on v3 feature matrix (max pain fix + dead GIFT features removed).

Runs: E3 labels, 25 features, shallow ensemble + gap routing, retrain every 21 days,
threshold sweep on Pareto frontier points. Compares vs previous (pre-fix) results.
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import psycopg2
from pathlib import Path

from src.preopen_features import PREOPEN_FEATURE_COLS
from src.comprehensive_sweep import (
    walk_forward_ensemble_gap_routed,
    run_stage2,
    evaluate,
    classify_gap,
    XGB_CONFIGS,
    E3_LABEL_MAP,
)
from src.v2_features import _safe_float

# Champion config: shallow XGB (C), walk-forward 126/21
CHAMPION_XGB = XGB_CONFIGS["C (shallow)"]
MIN_TRAIN = 126
RETRAIN_EVERY = 21

# Pareto frontier threshold combos to test
THRESHOLD_COMBOS = [
    (0.50, 0.50),
    (0.55, 0.60),
    (0.60, 0.70),
    (0.65, 0.65),
    (0.70, 0.70),
]


def load_v3_data():
    base = Path(__file__).resolve().parent.parent / "data"
    v3_path = base / "preopen_feature_matrix_v3.csv"
    if not v3_path.exists():
        raise FileNotFoundError(f"v3 matrix not found: {v3_path}. Run rebuild_feature_matrix_batch.py first.")

    v3 = pd.read_csv(v3_path, parse_dates=["date"])
    v3["date"] = v3["date"].dt.date
    print(f"Loaded v3 matrix: {len(v3)} rows x {len(v3.columns)} cols")

    conn = psycopg2.connect(host="localhost", user="me", password="algotrix", dbname="atdb")
    try:
        labels = pd.read_sql(
            "SELECT date, coincident_label, nifty_return FROM regime_ground_truth ORDER BY date",
            conn,
        )
    finally:
        conn.close()

    labels["date"] = pd.to_datetime(labels["date"]).dt.date

    df = labels.merge(v3, on="date", how="inner", suffixes=("", "_v3")).sort_values("date").reset_index(drop=True)
    df = df[df["coincident_label"].isin(["Trend-Up", "Range", "Trend-Down"])].reset_index(drop=True)

    print(f"After merge with labels: {len(df)} days")
    dist = df["coincident_label"].value_counts()
    for lbl, cnt in dist.items():
        print(f"  {lbl:<12} {cnt:>5} ({cnt/len(df)*100:.1f}%)")
    return df


def load_v2_data():
    """Load old v2 matrix for comparison."""
    base = Path(__file__).resolve().parent.parent / "data"
    v2_path = base / "preopen_feature_matrix_v2.csv"
    if not v2_path.exists():
        print("  (v2 matrix not found, skipping comparison)")
        return None

    v2 = pd.read_csv(v2_path, parse_dates=["date"])
    v2["date"] = v2["date"].dt.date

    conn = psycopg2.connect(host="localhost", user="me", password="algotrix", dbname="atdb")
    try:
        labels = pd.read_sql(
            "SELECT date, coincident_label, nifty_return FROM regime_ground_truth ORDER BY date",
            conn,
        )
    finally:
        conn.close()

    labels["date"] = pd.to_datetime(labels["date"]).dt.date

    df = labels.merge(v2, on="date", how="inner", suffixes=("", "_v2")).sort_values("date").reset_index(drop=True)
    df = df[df["coincident_label"].isin(["Trend-Up", "Range", "Trend-Down"])].reset_index(drop=True)
    return df


def run_threshold_sweep(label, df, feat_cols):
    """Run champion model with threshold sweep, return list of result dicts."""
    y_3class = df["coincident_label"].map(E3_LABEL_MAP).values.astype(int)
    nifty_returns = df["nifty_return"].fillna(0).values

    gap_col = "gift_overnight_gap_pct"
    gap_values = df[gap_col].values if gap_col in df.columns else np.full(len(df), np.nan)

    cols = [c for c in feat_cols if c in df.columns]
    X = df[cols].fillna(0).values

    print(f"\n  {label}: {len(cols)} features, {len(df)} days")
    print(f"  Features: {cols}")

    # Stage 1: ensemble + gap routing
    y_s1 = np.where(y_3class == 1, 0, 1).astype(int)
    print("  Running Stage 1 (ensemble + gap routing)...")
    s1_pred, s1_conf = walk_forward_ensemble_gap_routed(
        X, y_s1, gap_values, CHAMPION_XGB, MIN_TRAIN, RETRAIN_EVERY
    )

    results = []
    for s1t, s2t in THRESHOLD_COMBOS:
        final_pred, s2_pred, s2_conf = run_stage2(
            X, y_3class, s1_pred, s1_conf, s1t, s2t,
            CHAMPION_XGB, MIN_TRAIN, RETRAIN_EVERY,
        )
        r = evaluate(
            f"S1={s1t:.2f}/S2={s2t:.2f}",
            y_3class, s1_pred, s1_conf, s2_pred, s2_conf,
            final_pred, nifty_returns, MIN_TRAIN,
        )
        r["s1_threshold"] = s1t
        r["s2_threshold"] = s2t
        results.append(r)

    return results


def print_table(results, title):
    print(f"\n{'=' * 100}")
    print(f"  {title}")
    print(f"{'=' * 100}")
    print(f"  {'Thresholds':<20} {'S1 Acc':>7} {'S2 Acc':>7} {'CommAcc':>8} {'Comm%':>7}"
          f"  {'Up%':>7} {'Down%':>7} {'Spread':>7}")
    print("  " + "-" * 95)
    for r in results:
        print(f"  {r['name']:<20} {r['s1_acc']:>6.1%} {r['s2_acc']:>6.1%} "
              f"{r['committed_acc']:>7.1%} {r['committed_pct']:>6.1f}%"
              f"  {r['up_mean']:>+6.3f} {r['down_mean']:>+6.3f} {r['spread']:>6.3f}")


def main():
    print("=" * 100)
    print("  CHAMPION MODEL RERUN — v3 Feature Matrix (max pain fix + dead GIFT removed)")
    print("  Config: Shallow XGB (C), Ensemble + Gap Routing, 126/21 walk-forward")
    print("=" * 100)

    # v3: 25 features (PREOPEN_FEATURE_COLS without dead GIFT features)
    v3_feat_cols = PREOPEN_FEATURE_COLS  # already updated to 25

    # v2 baseline: use old 27-feature set (include the dead GIFT features)
    v2_feat_cols_27 = list(PREOPEN_FEATURE_COLS)  # copy current 25
    # Re-add the dead features for v2 comparison
    v2_feat_cols_27.insert(2, "gift_overnight_oi_change_pct")
    v2_feat_cols_27.insert(4, "gift_overnight_vol_delta")

    # --- Load v3 data ---
    print("\n--- Loading v3 data ---")
    df_v3 = load_v3_data()

    # --- Run v3 sweep ---
    v3_results = run_threshold_sweep("v3 (25 feat, fixed max pain)", df_v3, v3_feat_cols)
    print_table(v3_results, "v3 RESULTS — 25 Features, Fixed Max Pain")

    # --- Load v2 data for comparison ---
    print("\n\n--- Loading v2 data (pre-fix baseline) ---")
    df_v2 = load_v2_data()

    if df_v2 is not None:
        v2_results = run_threshold_sweep("v2 (27 feat, buggy max pain)", df_v2, v2_feat_cols_27)
        print_table(v2_results, "v2 RESULTS — 27 Features, Buggy Max Pain (BASELINE)")

        # --- Comparison ---
        print(f"\n{'=' * 100}")
        print("  COMPARISON: v3 vs v2")
        print(f"{'=' * 100}")
        print(f"  {'Thresholds':<20} {'v2 CommAcc':>10} {'v3 CommAcc':>10} {'Delta':>8}"
              f"  {'v2 Spread':>10} {'v3 Spread':>10} {'Delta':>8}")
        print("  " + "-" * 95)
        for r2, r3 in zip(v2_results, v3_results):
            acc_delta = r3["committed_acc"] - r2["committed_acc"]
            spread_delta = r3["spread"] - r2["spread"]
            print(f"  {r3['name']:<20} {r2['committed_acc']:>9.1%} {r3['committed_acc']:>9.1%} "
                  f"{acc_delta:>+7.1%}"
                  f"  {r2['spread']:>9.3f} {r3['spread']:>9.3f} {spread_delta:>+7.3f}")

    # Feature coverage check
    print(f"\n{'=' * 100}")
    print("  FEATURE COVERAGE (v3 matrix)")
    print(f"{'=' * 100}")
    for col in v3_feat_cols:
        if col in df_v3.columns:
            nn = df_v3[col].notna().sum()
            print(f"  {col:<40} {nn:>5}/{len(df_v3)} ({nn/len(df_v3)*100:.1f}%)")
        else:
            print(f"  {col:<40} MISSING!")

    print("\nDone.")


if __name__ == "__main__":
    main()
