"""Compare pre-open XGBoost: 27 original features vs 39 enriched features, both with E3 labels."""

import numpy as np
import pandas as pd
import psycopg2
from pathlib import Path
from collections import Counter

from xgboost import XGBClassifier

LABEL_MAP = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}
MIN_TRAIN = 126
RETRAIN_EVERY = 63

# 27 original features (v1)
V1_FEATURE_COLS = [
    "gift_overnight_gap_pct", "gift_overnight_range_pct", "gift_overnight_oi_change_pct",
    "gift_overnight_volume_conviction", "gift_overnight_vol_delta",
    "prev_nifty_return", "prev_nifty_return_5d", "prev_nifty_return_20d",
    "prev_vix_close", "prev_vix_change_pct",
    "prev_ad_ratio", "prev_breadth_turnover_weighted",
    "prev_pcr_oi", "prev_max_pain_distance_pct",
    "prev_fii_net_idx_fut", "prev_fii_net_total", "prev_dii_net_total",
    "prev_fii_options_skew",
    "prev_index_divergence_500", "prev_index_divergence_midcap",
    "prev_coincident_regime",
    "sp500_overnight_return", "usdinr_overnight_change",
    "day_of_week", "days_to_monthly_expiry", "is_expiry_week",
    "prev_day_range_pct",
]

# 39 enriched features (v2) = v1 + 12 new
V2_EXTRA_COLS = [
    "prev_nifty_futures_basis_pct", "prev_nifty_fut_oi_change_pct",
    "prev_nifty_fut_volume_ratio", "prev_pcr_oi_change",
    "prev_max_oi_call_distance_pct", "prev_max_oi_put_distance_pct",
    "prev_midcap_vs_nifty", "prev_smallcap_vs_nifty",
    "prev_bank_vs_nifty", "prev_defensive_vs_cyclical",
    "prev_trade_intensity", "prev_turnover_top10_share",
]
V2_FEATURE_COLS = V1_FEATURE_COLS + V2_EXTRA_COLS


def get_conn():
    return psycopg2.connect(host="localhost", user="me", password="algotrix", dbname="atdb")


def load_labels():
    conn = get_conn()
    df = pd.read_sql(
        "SELECT date, coincident_label FROM regime_ground_truth ORDER BY date", conn
    )
    conn.close()
    return df


def walk_forward_xgb(X_arr, y_arr):
    n = len(y_arr)
    if n < MIN_TRAIN + 10:
        return None

    preds = np.full(n, np.nan)
    probs = np.full((n, 3), np.nan)
    model = None
    last_train_end = -1

    for i in range(MIN_TRAIN, n):
        if model is None or (i - last_train_end) >= RETRAIN_EVERY:
            model = XGBClassifier(
                max_depth=4, n_estimators=200, learning_rate=0.05,
                subsample=0.8, use_label_encoder=False,
                eval_metric="mlogloss", objective="multi:softprob",
                num_class=3, verbosity=0, random_state=42,
            )
            model.fit(X_arr[:i], y_arr[:i])
            last_train_end = i

        preds[i] = model.predict(X_arr[i:i+1])[0]
        probs[i] = model.predict_proba(X_arr[i:i+1])[0]

    test_mask = ~np.isnan(preds)
    y_test = y_arr[test_mask]
    p_test = preds[test_mask].astype(int)

    accuracy = (y_test == p_test).mean()
    majority = Counter(y_test).most_common(1)[0][0]
    baseline = (y_test == majority).mean()

    probs_test = probs[test_mask]
    max_prob = probs_test.max(axis=1)
    high_conf_mask = max_prob >= 0.70
    high_conf_pct = high_conf_mask.mean() * 100
    high_conf_acc = (
        (y_test[high_conf_mask] == p_test[high_conf_mask]).mean()
        if high_conf_mask.sum() > 0 else 0.0
    )

    return {
        "accuracy": accuracy,
        "baseline": baseline,
        "margin": accuracy - baseline,
        "n_test": int(test_mask.sum()),
        "high_conf_acc": high_conf_acc,
        "high_conf_pct": high_conf_pct,
        "model": model,
    }


def main():
    print("=" * 70)
    print("  27 vs 39 Features — Pre-Open XGBoost Walk-Forward (E3 Labels)")
    print("=" * 70)

    base = Path(__file__).resolve().parent.parent / "data"
    v1_path = base / "preopen_feature_matrix.csv"
    v2_path = base / "preopen_feature_matrix_v2.csv"

    print(f"\nLoading v1 matrix: {v1_path}")
    v1_df = pd.read_csv(v1_path)
    print(f"  {len(v1_df)} rows, {len(v1_df.columns)} columns")

    print(f"Loading v2 matrix: {v2_path}")
    v2_df = pd.read_csv(v2_path)
    print(f"  {len(v2_df)} rows, {len(v2_df.columns)} columns")

    print("Loading E3 labels from DB...")
    labels = load_labels()
    print(f"  {len(labels)} label rows")

    # Merge each matrix with labels
    configs = {}
    for name, df, feat_cols in [
        ("27-feature (v1)", v1_df, V1_FEATURE_COLS),
        ("39-feature (v2)", v2_df, V2_FEATURE_COLS),
    ]:
        df["date"] = pd.to_datetime(df["date"]).dt.date
        labels["date"] = pd.to_datetime(labels["date"]).dt.date
        merged = labels.merge(df, on="date", how="inner")

        available = [c for c in feat_cols if c in merged.columns]
        X = merged[available].fillna(0).values
        y = merged["coincident_label"].map(LABEL_MAP)

        valid = y.notna()
        X = X[valid]
        y = y[valid].values.astype(int)

        configs[name] = (X, y, available)
        print(f"\n{name}: {len(y)} days, {len(available)} features")

    # Run walk-forward on both
    print(f"\nWalk-forward params: min_train={MIN_TRAIN}, retrain_every={RETRAIN_EVERY}")
    print(f"  max_depth=4, n_estimators=200, lr=0.05, subsample=0.8\n")

    results = {}
    for name, (X, y, feat_cols) in configs.items():
        print(f"Running {name}...")
        res = walk_forward_xgb(X, y)
        if res:
            results[name] = res
            print(f"  Accuracy: {res['accuracy']:.1%} | Baseline: {res['baseline']:.1%} | "
                  f"Margin: {res['margin']:+.1%} | N={res['n_test']}")
            print(f"  High-conf (>=70%): {res['high_conf_acc']:.1%} on {res['high_conf_pct']:.1f}% of days")
        else:
            print(f"  SKIPPED (insufficient data)")
        print()

    # Summary table
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"{'Model':<20} {'Accuracy':>10} {'Baseline':>10} {'Margin':>10} {'HiConf Acc':>12} {'HiConf %':>10} {'N':>6}")
    print("-" * 80)
    for name, res in results.items():
        print(f"{name:<20} {res['accuracy']:>9.1%} {res['baseline']:>9.1%} {res['margin']:>+9.1%} "
              f"{res['high_conf_acc']:>11.1%} {res['high_conf_pct']:>9.1f}% {res['n_test']:>5}")

    # Top 15 feature importance for 39-feature model
    v2_name = "39-feature (v2)"
    if v2_name in results:
        model = results[v2_name]["model"]
        feat_cols = configs[v2_name][2]
        importances = model.feature_importances_
        top_idx = np.argsort(importances)[::-1][:15]

        print(f"\n{'=' * 70}")
        print(f"  TOP 15 FEATURE IMPORTANCE — 39-feature model (final retrain)")
        print(f"{'=' * 70}")
        print(f"{'Rank':<6} {'Feature':<40} {'Importance':>10} {'New?':>6}")
        print("-" * 64)
        for rank, idx in enumerate(top_idx, 1):
            feat = feat_cols[idx]
            imp = importances[idx]
            is_new = "  *" if feat in V2_EXTRA_COLS else ""
            print(f"{rank:<6} {feat:<40} {imp:>10.4f} {is_new:>6}")

    print()


if __name__ == "__main__":
    main()
