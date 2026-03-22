"""
E5 Variants Comparison: E3 baseline vs E5a (relaxed rule-based) vs E5b (cluster-based)

E3:  Current best labels (coincident_label from regime_ground_truth)
E5a: Demote Trend-Up/Down → Range if weak return + above-avg volume (churning)
E5b: Demote Trend-Up/Down → Range using unsupervised cluster assignments
"""

import numpy as np
import pandas as pd
import psycopg2
from xgboost import XGBClassifier

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PARAMS = dict(host="localhost", user="me", password="algotrix", dbname="atdb")
FM_PATH = "data/preopen_feature_matrix.csv"
CLUSTER_PATH = "data/cluster_analysis.csv"

LABEL_MAP = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}
LABEL_NAMES = {0: "Trend-Down", 1: "Range", 2: "Trend-Up"}

FEATURE_COLS = [
    "gift_overnight_gap_pct", "gift_overnight_range_pct",
    "gift_overnight_oi_change_pct", "gift_overnight_volume_conviction",
    "gift_overnight_vol_delta", "prev_nifty_return", "prev_nifty_return_5d",
    "prev_nifty_return_20d", "prev_vix_close", "prev_vix_change_pct",
    "prev_ad_ratio", "prev_breadth_turnover_weighted", "prev_pcr_oi",
    "prev_max_pain_distance_pct", "prev_fii_net_idx_fut", "prev_fii_net_total",
    "prev_dii_net_total", "prev_fii_options_skew", "prev_index_divergence_500",
    "prev_index_divergence_midcap", "prev_coincident_regime",
    "sp500_overnight_return", "usdinr_overnight_change", "day_of_week",
    "days_to_monthly_expiry", "is_expiry_week", "prev_day_range_pct",
]

MIN_TRAIN = 126
RETRAIN_EVERY = 63
XGB_PARAMS = dict(
    max_depth=4, n_estimators=200, learning_rate=0.05, subsample=0.8,
    use_label_encoder=False, eval_metric="mlogloss",
    objective="multi:softprob", num_class=3, verbosity=0, random_state=42,
)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_base_data():
    """Load feature matrix + ground truth labels + nifty_return."""
    fm = pd.read_csv(FM_PATH, parse_dates=["date"])
    feature_cols = [c for c in FEATURE_COLS if c in fm.columns]
    print(f"Features available: {len(feature_cols)} / {len(FEATURE_COLS)}")

    conn = psycopg2.connect(**DB_PARAMS)
    gt = pd.read_sql(
        "SELECT date, coincident_label, nifty_return FROM regime_ground_truth ORDER BY date",
        conn, parse_dates=["date"],
    )
    conn.close()

    # Both fm and gt have nifty_return; drop fm's copy before merging
    fm = fm.drop(columns=["nifty_return"], errors="ignore")
    df = fm.merge(gt, on="date", how="inner").sort_values("date").reset_index(drop=True)
    df["e3_label"] = df["coincident_label"].map(LABEL_MAP)
    print(f"Merged rows: {len(df)}")
    return df, feature_cols


def load_volume_ratio():
    """Compute market-wide daily turnover / rolling 20-day avg from nse_cm_bhavcopy."""
    conn = psycopg2.connect(**DB_PARAMS)
    tv = pd.read_sql(
        "SELECT date, SUM(traded_value) as daily_turnover "
        "FROM nse_cm_bhavcopy GROUP BY date ORDER BY date",
        conn, parse_dates=["date"],
    )
    conn.close()
    tv["rolling_avg_20"] = tv["daily_turnover"].rolling(20, min_periods=10).mean()
    tv["volume_ratio"] = tv["daily_turnover"] / tv["rolling_avg_20"]
    return tv[["date", "volume_ratio"]].dropna()


def load_clusters():
    """Load cluster assignments from cluster_analysis.csv."""
    cl = pd.read_csv(CLUSTER_PATH, parse_dates=["date"])
    return cl[["date", "best_cluster"]]

# ---------------------------------------------------------------------------
# Label construction
# ---------------------------------------------------------------------------

def make_e5a_labels(df, vol_df):
    """E5a: Demote Trend-Up/Down → Range if weak return + above-avg volume."""
    merged = df.merge(vol_df, on="date", how="left")
    labels = merged["e3_label"].copy()

    abs_ret = merged["nifty_return"].abs()
    rolling_p50 = abs_ret.rolling(63, min_periods=20).median()

    vr = merged["volume_ratio"]

    trend_mask = labels.isin([0, 2])  # Trend-Down or Trend-Up
    weak_return = abs_ret < rolling_p50
    high_volume = vr > 1.05

    demote_mask = trend_mask & weak_return & high_volume
    labels[demote_mask] = 1  # → Range

    n_demoted = demote_mask.sum()
    print(f"  E5a demotions: {n_demoted}")
    return labels.values, n_demoted


def make_e5b_labels(df, cluster_df):
    """E5b: Demote Trend-Up/Down → Range for days in churning cluster (best_cluster=3)."""
    merged = df.merge(cluster_df, on="date", how="left")
    labels = merged["e3_label"].copy()

    trend_mask = labels.isin([0, 2])
    cluster_mask = merged["best_cluster"] == 3

    demote_mask = trend_mask & cluster_mask
    labels[demote_mask] = 1

    n_demoted = demote_mask.sum()
    print(f"  E5b demotions: {n_demoted}")
    return labels.values, n_demoted

# ---------------------------------------------------------------------------
# Walk-forward XGBoost
# ---------------------------------------------------------------------------

def walk_forward(df, feature_cols, targets):
    """Walk-forward XGBoost, returns DataFrame of predictions."""
    X = df[feature_cols].fillna(0).values
    y = targets
    valid_mask = ~np.isnan(y) if isinstance(y, np.ndarray) else ~pd.isna(y)
    nifty_ret = df["nifty_return"].values

    results = []
    model = None
    last_train_end = -1

    for i in range(MIN_TRAIN, len(X)):
        if not valid_mask[i]:
            continue

        if model is None or (i - last_train_end) >= RETRAIN_EVERY:
            train_idx = np.where(valid_mask[:i])[0]
            if len(train_idx) < MIN_TRAIN:
                continue
            X_train, y_train = X[train_idx], y[train_idx]
            model = XGBClassifier(**XGB_PARAMS)
            model.fit(X_train, y_train)
            last_train_end = i

        proba = model.predict_proba(X[i:i+1])[0]
        pred = int(np.argmax(proba))
        conf = float(proba.max())

        results.append({
            "date": df.iloc[i]["date"],
            "actual": int(y[i]),
            "predicted": pred,
            "confidence": conf,
            "nifty_return": float(nifty_ret[i]),
        })

    return pd.DataFrame(results)

# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(name, res_df, n_demoted=0, class_dist=None):
    """Print evaluation metrics."""
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

    if class_dist is not None:
        print(f"\n  Class distribution (training labels):")
        for lbl_id in sorted(class_dist.index):
            pct = class_dist[lbl_id] / class_dist.sum() * 100
            print(f"    {LABEL_NAMES.get(lbl_id, lbl_id)}: {class_dist[lbl_id]} ({pct:.1f}%)")
    if n_demoted > 0:
        print(f"  Demotions from E3: {n_demoted}")

    n = len(res_df)
    acc = (res_df["actual"] == res_df["predicted"]).mean()
    baseline = res_df["actual"].value_counts().max() / n
    margin = acc - baseline

    print(f"\n  N predictions:  {n}")
    print(f"  Accuracy:       {acc:.1%}")
    print(f"  Baseline:       {baseline:.1%}")
    print(f"  Margin:         {margin:+.1%}")

    # High-confidence (>=70%)
    hc = res_df[res_df["confidence"] >= 0.70]
    if len(hc) > 0:
        hc_acc = (hc["actual"] == hc["predicted"]).mean()
        hc_pct = len(hc) / n * 100
        print(f"\n  High-conf (>=70%): {hc_acc:.1%} acc, {hc_pct:.1f}% of days ({len(hc)} days)")
    else:
        print(f"\n  High-conf (>=70%): no predictions")

    # Per-class accuracy
    print(f"\n  Per-class accuracy:")
    for lbl_id in sorted(res_df["actual"].unique()):
        subset = res_df[res_df["actual"] == lbl_id]
        cls_acc = (subset["actual"] == subset["predicted"]).mean()
        print(f"    {LABEL_NAMES.get(lbl_id, lbl_id)}: {cls_acc:.1%} ({len(subset)} days)")

    # Return separation by PREDICTED class
    print(f"\n  Return separation (mean nifty_return by predicted class):")
    for lbl_id in sorted(res_df["predicted"].unique()):
        subset = res_df[res_df["predicted"] == lbl_id]
        mean_ret = subset["nifty_return"].mean() * 100  # to bps-ish
        print(f"    Predicted {LABEL_NAMES.get(lbl_id, lbl_id)}: {mean_ret:+.3f}% ({len(subset)} days)")

    # Confusion matrix
    print(f"\n  Confusion matrix (rows=actual, cols=predicted):")
    labels = sorted(set(res_df["actual"].unique()) | set(res_df["predicted"].unique()))
    cm = pd.crosstab(
        res_df["actual"].map(LABEL_NAMES),
        res_df["predicted"].map(LABEL_NAMES),
    )
    print(cm.to_string(index=True))

    return {"accuracy": acc, "baseline": baseline, "margin": margin,
            "hc_acc": (hc["actual"] == hc["predicted"]).mean() if len(hc) > 0 else None,
            "hc_pct": len(hc) / n * 100 if len(hc) > 0 else 0,
            "hc_n": len(hc), "n": n}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading data...")
    df, feature_cols = load_base_data()
    vol_df = load_volume_ratio()
    cluster_df = load_clusters()

    # --- E3 baseline ---
    print("\n--- E3 (baseline) ---")
    e3_targets = df["e3_label"].values.astype(float)
    e3_dist = pd.Series(e3_targets).value_counts().sort_index()

    # --- E5a labels ---
    print("\n--- E5a (relaxed rule-based) ---")
    e5a_targets, e5a_demoted = make_e5a_labels(df, vol_df)
    e5a_targets = e5a_targets.astype(float)
    e5a_dist = pd.Series(e5a_targets).value_counts().sort_index()

    # --- E5b labels ---
    print("\n--- E5b (cluster-based) ---")
    e5b_targets, e5b_demoted = make_e5b_labels(df, cluster_df)
    e5b_targets = e5b_targets.astype(float)
    e5b_dist = pd.Series(e5b_targets).value_counts().sort_index()

    # --- Walk-forward for all three ---
    print("\n" + "="*60)
    print("Running walk-forward XGBoost for E3...")
    e3_res = walk_forward(df, feature_cols, e3_targets)

    print("Running walk-forward XGBoost for E5a...")
    e5a_res = walk_forward(df, feature_cols, e5a_targets)

    print("Running walk-forward XGBoost for E5b...")
    e5b_res = walk_forward(df, feature_cols, e5b_targets)

    # --- Evaluate ---
    r3 = evaluate("E3 — Baseline (coincident_label)", e3_res, class_dist=e3_dist)
    r5a = evaluate("E5a — Relaxed rule-based filter", e5a_res, n_demoted=e5a_demoted, class_dist=e5a_dist)
    r5b = evaluate("E5b — Cluster-based remapping", e5b_res, n_demoted=e5b_demoted, class_dist=e5b_dist)

    # --- Summary comparison ---
    print(f"\n{'='*60}")
    print(f"  SUMMARY COMPARISON")
    print(f"{'='*60}")
    print(f"{'Variant':<12} {'Acc':>7} {'Base':>7} {'Margin':>8} {'HC Acc':>8} {'HC %':>6} {'HC N':>6}")
    print(f"{'-'*57}")
    for name, r in [("E3", r3), ("E5a", r5a), ("E5b", r5b)]:
        hc_str = f"{r['hc_acc']:.1%}" if r['hc_acc'] is not None else "N/A"
        print(f"{name:<12} {r['accuracy']:>6.1%} {r['baseline']:>6.1%} {r['margin']:>+7.1%} {hc_str:>8} {r['hc_pct']:>5.1f}% {r['hc_n']:>5}")


if __name__ == "__main__":
    main()
