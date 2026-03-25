"""Pre-open gap feature test against the regime classifier.

Computes market-wide pre-open gap features from nse_cm_bhavcopy,
merges with existing 27 pre-open features, and runs walk-forward
XGBoost to test whether gap features improve regime prediction.
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import psycopg2
from pathlib import Path
from scipy import stats
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

DB_DSN = "host=localhost user=me password=algotrix dbname=atdb"
E3_LABEL_MAP = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}
BASE_DIR = Path(__file__).resolve().parent.parent / "data"

LGBM_BASE = dict(
    max_depth=4, n_estimators=200, learning_rate=0.05,
    subsample=0.8, num_leaves=31, verbose=-1, random_state=42,
)
XGB_PARAMS = dict(
    max_depth=4, n_estimators=200, learning_rate=0.05,
    subsample=0.8, use_label_encoder=False, verbosity=0, random_state=42,
)

GAP_FEATURE_COLS = [
    "nifty_gap_pct",
    "banknifty_gap_pct",
    "market_gap_mean",
    "market_gap_median",
    "gap_up_count",
    "gap_down_count",
    "gap_flat_count",
    "gap_breadth_ratio",
    "gap_skew",
    "gap_std",
    "large_gap_up_count",
    "large_gap_down_count",
    "volume_weighted_gap",
    "gap_vs_vix",
]


# =============================================================================
# 1. Compute gap features
# =============================================================================

def compute_gap_features():
    """Compute per-day market-wide gap features from bhavcopy + indices."""
    conn = psycopg2.connect(DB_DSN)
    try:
        # Load regime ground truth dates
        gt = pd.read_sql(
            "SELECT date FROM regime_ground_truth ORDER BY date", conn
        )
        gt_dates = sorted(gt["date"].tolist())
        print(f"  Ground truth dates: {len(gt_dates)} ({gt_dates[0]} to {gt_dates[-1]})")

        # Load all bhavcopy data at once (faster than per-day queries)
        print("  Loading nse_cm_bhavcopy...")
        bhav = pd.read_sql(
            """SELECT date, open, prev_close, volume
               FROM nse_cm_bhavcopy
               WHERE date >= %s AND date <= %s
                 AND prev_close > 0 AND open > 0""",
            conn, params=[gt_dates[0], gt_dates[-1]],
        )
        print(f"  Loaded {len(bhav):,} bhavcopy rows")

        # Compute per-stock gap
        bhav["gap_pct"] = (bhav["open"] / bhav["prev_close"] - 1) * 100

        # Load index data for Nifty 50 and Nifty Bank
        print("  Loading nse_indices_daily...")
        idx = pd.read_sql(
            """SELECT date, index, open, close
               FROM nse_indices_daily
               WHERE index IN ('Nifty 50', 'Nifty Bank')
                 AND date >= %s AND date <= %s
               ORDER BY date""",
            conn, params=[gt_dates[0], gt_dates[-1]],
        )

        # Build prev_close for indices (close of previous trading day)
        nifty_idx = idx[idx["index"] == "Nifty 50"].sort_values("date").copy()
        nifty_idx["prev_close"] = nifty_idx["close"].shift(1)
        nifty_idx["gap_pct"] = (nifty_idx["open"] / nifty_idx["prev_close"] - 1) * 100
        nifty_gap = nifty_idx.set_index("date")["gap_pct"].to_dict()

        bank_idx = idx[idx["index"] == "Nifty Bank"].sort_values("date").copy()
        bank_idx["prev_close"] = bank_idx["close"].shift(1)
        bank_idx["gap_pct"] = (bank_idx["open"] / bank_idx["prev_close"] - 1) * 100
        bank_gap = bank_idx.set_index("date")["gap_pct"].to_dict()

        # Load VIX for gap_vs_vix
        vix = pd.read_sql(
            """SELECT date, close FROM nse_indices_daily
               WHERE index = 'India VIX'
                 AND date >= %s AND date <= %s
               ORDER BY date""",
            conn, params=[gt_dates[0], gt_dates[-1]],
        )
        if vix.empty:
            # Try alternative name
            vix = pd.read_sql(
                """SELECT date, close FROM nse_indices_daily
                   WHERE index ILIKE '%%vix%%'
                     AND date >= %s AND date <= %s
                   ORDER BY date LIMIT 5""",
                conn, params=[gt_dates[0], gt_dates[-1]],
            )
            if not vix.empty:
                vix_index_name = pd.read_sql(
                    "SELECT DISTINCT index FROM nse_indices_daily WHERE index ILIKE '%%vix%%' LIMIT 1",
                    conn,
                ).iloc[0]["index"]
                print(f"  Using VIX index: '{vix_index_name}'")
                vix = pd.read_sql(
                    f"""SELECT date, close FROM nse_indices_daily
                        WHERE index = %s AND date >= %s AND date <= %s
                        ORDER BY date""",
                    conn, params=[vix_index_name, gt_dates[0], gt_dates[-1]],
                )

        vix_by_date = {}
        if not vix.empty:
            vix = vix.sort_values("date")
            vix["prev_close"] = vix["close"].shift(1)
            vix_by_date = vix.set_index("date")["prev_close"].to_dict()

    finally:
        conn.close()

    # Group bhavcopy by date for vectorized computation
    print("  Computing gap features per day...")
    grouped = bhav.groupby("date")

    rows = []
    for dt in gt_dates:
        row = {"date": dt}

        # Index gaps
        row["nifty_gap_pct"] = nifty_gap.get(dt, np.nan)
        row["banknifty_gap_pct"] = bank_gap.get(dt, np.nan)

        if dt not in grouped.groups:
            for col in GAP_FEATURE_COLS:
                if col not in row:
                    row[col] = np.nan
            rows.append(row)
            continue

        day_data = grouped.get_group(dt)
        gaps = day_data["gap_pct"].dropna()
        volumes = day_data.loc[gaps.index, "volume"].fillna(0)

        if len(gaps) == 0:
            for col in GAP_FEATURE_COLS:
                if col not in row:
                    row[col] = np.nan
            rows.append(row)
            continue

        row["market_gap_mean"] = gaps.mean()
        row["market_gap_median"] = gaps.median()
        row["gap_up_count"] = (gaps > 0.1).sum()
        row["gap_down_count"] = (gaps < -0.1).sum()
        row["gap_flat_count"] = ((gaps >= -0.1) & (gaps <= 0.1)).sum()
        total = row["gap_up_count"] + row["gap_down_count"] + row["gap_flat_count"]
        row["gap_breadth_ratio"] = row["gap_up_count"] / total if total > 0 else 0.5
        row["gap_skew"] = float(stats.skew(gaps)) if len(gaps) >= 3 else 0.0
        row["gap_std"] = gaps.std()
        row["large_gap_up_count"] = (gaps > 1.0).sum()
        row["large_gap_down_count"] = (gaps < -1.0).sum()

        vol_sum = volumes.sum()
        row["volume_weighted_gap"] = (gaps.values * volumes.values).sum() / vol_sum if vol_sum > 0 else 0.0

        prev_vix = vix_by_date.get(dt, np.nan)
        nifty_g = row["nifty_gap_pct"]
        if not np.isnan(prev_vix) and prev_vix > 0 and not np.isnan(nifty_g):
            row["gap_vs_vix"] = nifty_g / prev_vix
        else:
            row["gap_vs_vix"] = np.nan

        rows.append(row)

    gap_df = pd.DataFrame(rows)
    print(f"  Gap features computed: {len(gap_df)} days, {len(GAP_FEATURE_COLS)} features")

    # Check coverage
    for col in GAP_FEATURE_COLS:
        non_null = gap_df[col].notna().sum()
        print(f"    {col:<30} {non_null:>5}/{len(gap_df)} non-null")

    return gap_df


# =============================================================================
# 2. Load and merge data
# =============================================================================

def load_data():
    """Load labels, existing features, and gap features."""
    conn = psycopg2.connect(DB_DSN)
    try:
        labels = pd.read_sql(
            "SELECT date, coincident_label, nifty_return FROM regime_ground_truth ORDER BY date",
            conn,
        )
    finally:
        conn.close()

    labels["date"] = pd.to_datetime(labels["date"]).dt.date

    # Load existing v1 features
    v1 = pd.read_csv(BASE_DIR / "preopen_feature_matrix.csv", parse_dates=["date"])
    v1["date"] = v1["date"].dt.date

    # Merge — v1 CSV has nifty_return/breadth_ratio cols that overlap with labels
    df = labels.merge(v1, on="date", how="inner", suffixes=("", "_v1")).sort_values("date").reset_index(drop=True)
    df = df[df["coincident_label"].isin(["Trend-Up", "Range", "Trend-Down"])].reset_index(drop=True)

    return df


# =============================================================================
# 3. Walk-forward evaluation (simplified from comprehensive_sweep)
# =============================================================================

def classify_gap(gap_val):
    if np.isnan(gap_val):
        return "missing"
    if gap_val > 0.003:
        return "gap_up"
    if gap_val < -0.003:
        return "gap_down"
    return "flat"


def walk_forward_ensemble(X, y_s1, gap_values, min_train=252, retrain_every=21):
    """Ensemble (XGB+LGBM+LogReg) with gap-routed buckets."""
    n = len(y_s1)
    s1_pred = np.full(n, np.nan)
    s1_conf = np.full(n, np.nan)
    gap_buckets = np.array([classify_gap(g) for g in gap_values])

    bucket_models = {}
    last_trains = {}
    global_models = None
    global_last_train = -1

    def _train(X_tr, y_tr):
        xgb = XGBClassifier(**XGB_PARAMS, objective="binary:logistic", eval_metric="logloss")
        xgb.fit(X_tr, y_tr)
        lgbm = LGBMClassifier(**LGBM_BASE)
        lgbm.fit(X_tr, y_tr)
        sc = StandardScaler()
        X_sc = sc.fit_transform(X_tr)
        lr = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
        lr.fit(X_sc, y_tr)
        return (xgb, lgbm, lr, sc)

    def _predict(models, xi):
        xgb, lgbm, lr, sc = models
        p1 = xgb.predict_proba(xi)[0]
        p2 = lgbm.predict_proba(xi)[0]
        p3 = lr.predict_proba(sc.transform(xi))[0]
        return (p1 + p2 + p3) / 3.0

    for i in range(min_train, n):
        if global_models is None or (i - global_last_train) >= retrain_every:
            global_models = _train(X[:i], y_s1[:i])
            global_last_train = i

        bucket = gap_buckets[i]
        if bucket != "missing":
            should_retrain = (bucket not in bucket_models) or (i - last_trains.get(bucket, -1)) >= retrain_every
            if should_retrain:
                mask = gap_buckets[:i] == bucket
                if mask.sum() >= min_train:
                    bucket_models[bucket] = _train(X[:i][mask], y_s1[:i][mask])
                    last_trains[bucket] = i

        xi = X[i:i+1]
        if bucket != "missing" and bucket in bucket_models:
            avg = _predict(bucket_models[bucket], xi)
        else:
            avg = _predict(global_models, xi)

        cls = int(avg[1] >= 0.5)
        s1_pred[i] = cls
        s1_conf[i] = avg[cls]

    return s1_pred, s1_conf


def run_stage2(X, y_3class, s1_pred, s1_conf, s1_threshold, s2_threshold,
               min_train=252, retrain_every=21):
    n = len(y_3class)
    y_s1 = np.where(y_3class == 1, 0, 1).astype(int)
    y_s2 = np.where(y_3class == 2, 1, 0).astype(int)
    trend_mask_full = y_s1 == 1

    final_pred = np.full(n, "", dtype=object)
    s2_pred = np.full(n, np.nan)
    s2_conf = np.full(n, np.nan)

    model_s2 = None
    last_train_s2 = -1

    for i in range(min_train, n):
        if model_s2 is None or (i - last_train_s2) >= retrain_every:
            trend_train = trend_mask_full[:i]
            if trend_train.sum() >= 20:
                model_s2 = XGBClassifier(
                    **XGB_PARAMS, objective="binary:logistic", eval_metric="logloss",
                )
                model_s2.fit(X[:i][trend_train], y_s2[:i][trend_train])
                last_train_s2 = i

        if np.isnan(s1_conf[i]):
            continue

        if s1_conf[i] < s1_threshold:
            final_pred[i] = "Uncertain"
            continue

        if s1_pred[i] == 0:
            final_pred[i] = "Range"
            continue

        if model_s2 is None:
            final_pred[i] = "Trend-Unknown"
            continue

        s2_prob = model_s2.predict_proba(X[i:i+1])[0]
        s2_class = int(s2_prob[1] >= 0.5)
        s2_confidence = s2_prob[s2_class]
        s2_pred[i] = s2_class
        s2_conf[i] = s2_confidence

        if s2_confidence < s2_threshold:
            final_pred[i] = "Trend-Unknown"
        elif s2_class == 1:
            final_pred[i] = "Trend-Up"
        else:
            final_pred[i] = "Trend-Down"

    return final_pred, s2_pred, s2_conf


def evaluate(name, y_3class, s1_pred, s1_conf, s2_pred, s2_conf, final_pred,
             nifty_returns, min_train):
    test_mask = np.arange(len(y_3class)) >= min_train
    y_s1_true = np.where(y_3class == 1, 0, 1)
    s1_valid = test_mask & ~np.isnan(s1_pred)
    s1_acc = (y_s1_true[s1_valid] == s1_pred[s1_valid].astype(int)).mean() if s1_valid.sum() > 0 else 0

    y_s2_true = np.where(y_3class == 2, 1, 0)
    s2_valid = test_mask & ~np.isnan(s2_pred) & (y_s1_true == 1)
    s2_acc = (y_s2_true[s2_valid] == s2_pred[s2_valid].astype(int)).mean() if s2_valid.sum() > 0 else 0

    test_preds = final_pred[test_mask]
    test_y = y_3class[test_mask]
    test_nr = nifty_returns[test_mask]

    mapping = {"Trend-Up": 2, "Trend-Down": 0, "Range": 1}
    committed_mask = np.array([p in mapping for p in test_preds])
    committed_pct = committed_mask.mean() * 100

    if committed_mask.sum() > 0:
        committed_y = test_y[committed_mask]
        committed_p = np.array([mapping[p] for p in test_preds[committed_mask]])
        committed_acc = (committed_y == committed_p).mean()
    else:
        committed_acc = 0

    return_sep = {}
    for cls_name in ["Trend-Up", "Trend-Down", "Range"]:
        cls_mask = test_preds == cls_name
        if cls_mask.sum() > 0:
            return_sep[cls_name] = test_nr[cls_mask].mean() * 100

    up_mean = return_sep.get("Trend-Up", 0)
    down_mean = return_sep.get("Trend-Down", 0)
    spread = up_mean - down_mean

    return {
        "name": name,
        "s1_acc": s1_acc,
        "s2_acc": s2_acc,
        "committed_acc": committed_acc,
        "committed_pct": committed_pct,
        "up_mean": up_mean,
        "down_mean": down_mean,
        "spread": spread,
    }


def run_pipeline(name, X, y_3class, gap_values, nifty_returns,
                 s1_thresh, s2_thresh, min_train=252, retrain_every=21):
    y_s1 = np.where(y_3class == 1, 0, 1).astype(int)
    s1_pred, s1_conf = walk_forward_ensemble(X, y_s1, gap_values, min_train, retrain_every)
    final_pred, s2_pred, s2_conf = run_stage2(
        X, y_3class, s1_pred, s1_conf, s1_thresh, s2_thresh, min_train, retrain_every)
    return evaluate(name, y_3class, s1_pred, s1_conf, s2_pred, s2_conf,
                    final_pred, nifty_returns, min_train)


# =============================================================================
# 4. Main
# =============================================================================

def main():
    print("=" * 100)
    print("  PRE-OPEN GAP FEATURES TEST")
    print("=" * 100)

    # Step 1: Compute gap features
    print("\n--- Computing gap features from bhavcopy ---")
    gap_df = compute_gap_features()

    # Step 2: Load existing data
    print("\n--- Loading existing features and labels ---")
    df = load_data()
    print(f"  Loaded {len(df)} days with existing features")

    # Merge gap features
    gap_df["date"] = pd.to_datetime(gap_df["date"]).dt.date if not isinstance(gap_df["date"].iloc[0], type(df["date"].iloc[0])) else gap_df["date"]
    df = df.merge(gap_df, on="date", how="left")
    print(f"  After merge: {len(df)} days")

    # Prepare arrays
    y_3class = df["coincident_label"].map(E3_LABEL_MAP).values.astype(int)
    nifty_returns = df["nifty_return"].fillna(0).values
    gap_col = "gift_overnight_gap_pct"
    gap_values = df[gap_col].values if gap_col in df.columns else np.full(len(df), np.nan)

    # Original 27 features
    from src.preopen_features import PREOPEN_FEATURE_COLS
    orig_cols = [c for c in PREOPEN_FEATURE_COLS[:27] if c in df.columns]
    # Combined: 27 + gap
    combined_cols = orig_cols + [c for c in GAP_FEATURE_COLS if c in df.columns]
    # Gap only
    gap_only_cols = [c for c in GAP_FEATURE_COLS if c in df.columns]

    print(f"\n  Feature sets:")
    print(f"    Original:  {len(orig_cols)} features")
    print(f"    Gap only:  {len(gap_only_cols)} features")
    print(f"    Combined:  {len(combined_cols)} features")

    X_orig = df[orig_cols].fillna(0).values
    X_combined = df[combined_cols].fillna(0).values
    X_gap_only = df[gap_only_cols].fillna(0).values

    dist = df["coincident_label"].value_counts()
    for lbl, cnt in dist.items():
        print(f"    {lbl:<12} {cnt:>5} ({cnt/len(df)*100:.1f}%)")

    # Step 3: Run comparisons at multiple thresholds
    threshold_pairs = [
        (0.50, 0.50, "No threshold"),
        (0.55, 0.55, "Moderate (0.55/0.55)"),
        (0.60, 0.60, "Strict (0.60/0.60)"),
        (0.65, 0.65, "High (0.65/0.65)"),
        (0.70, 0.70, "Very high (0.70/0.70)"),
    ]

    all_results = []

    for s1t, s2t, thr_name in threshold_pairs:
        print(f"\n{'='*100}")
        print(f"  THRESHOLD: S1={s1t:.2f}, S2={s2t:.2f} ({thr_name})")
        print(f"{'='*100}")

        configs = [
            ("Original 27 features", X_orig),
            ("27 + 14 gap features", X_combined),
            ("Gap features only (14)", X_gap_only),
        ]

        for feat_name, X_data in configs:
            print(f"  Running: {feat_name} ...", end=" ", flush=True)
            r = run_pipeline(
                f"{feat_name} @ {thr_name}",
                X_data, y_3class, gap_values, nifty_returns,
                s1_thresh=s1t, s2_thresh=s2t,
            )
            print(f"S1={r['s1_acc']:.1%} CommAcc={r['committed_acc']:.1%} "
                  f"Comm%={r['committed_pct']:.1f}% Spread={r['spread']:.3f}")
            r["threshold"] = thr_name
            r["s1_threshold"] = s1t
            r["s2_threshold"] = s2t
            r["feature_set"] = feat_name
            all_results.append(r)

    # Step 4: Summary table
    print(f"\n\n{'='*120}")
    print("  FULL RESULTS SUMMARY")
    print(f"{'='*120}")
    hdr = (f"  {'Variant':<50} {'S1 Acc':>7} {'S2 Acc':>7} {'CommAcc':>8} {'Comm%':>7}"
           f"  {'Up%':>7} {'Down%':>7} {'Spread':>7}")
    print(hdr)
    print("  " + "-" * 115)

    for r in all_results:
        print(f"  {r['name']:<50} {r['s1_acc']:>6.1%} {r['s2_acc']:>6.1%} "
              f"{r['committed_acc']:>7.1%} {r['committed_pct']:>6.1f}%"
              f"  {r['up_mean']:>+6.3f} {r['down_mean']:>+6.3f} {r['spread']:>6.3f}")

    # Comparison table: feature set vs threshold
    print(f"\n\n{'='*120}")
    print("  COMPARISON: Gap Features Impact by Threshold")
    print(f"{'='*120}")
    print(f"  {'Threshold':<25} {'Original CommAcc':>16} {'+ Gap CommAcc':>14} {'Gap Only':>10} {'Delta':>8}")
    print("  " + "-" * 80)

    for s1t, s2t, thr_name in threshold_pairs:
        orig_r = [r for r in all_results if r["feature_set"] == "Original 27 features" and r["s1_threshold"] == s1t][0]
        comb_r = [r for r in all_results if r["feature_set"] == "27 + 14 gap features" and r["s1_threshold"] == s1t][0]
        gap_r = [r for r in all_results if r["feature_set"] == "Gap features only (14)" and r["s1_threshold"] == s1t][0]
        delta = comb_r["committed_acc"] - orig_r["committed_acc"]
        print(f"  {thr_name:<25} {orig_r['committed_acc']:>15.1%} {comb_r['committed_acc']:>13.1%} "
              f"{gap_r['committed_acc']:>9.1%} {delta:>+7.1%}")

    # Save results
    results_df = pd.DataFrame(all_results)
    out_path = BASE_DIR / "gap_feature_results.csv"
    results_df.to_csv(out_path, index=False)
    print(f"\n  Results saved to {out_path}")

    # Save gap features for future use
    gap_out = BASE_DIR / "gap_features.csv"
    gap_df.to_csv(gap_out, index=False)
    print(f"  Gap features saved to {gap_out}")

    # Verdict
    print(f"\n{'='*120}")
    print("  VERDICT")
    print(f"{'='*120}")
    # Compare at moderate threshold
    mod_orig = [r for r in all_results if r["feature_set"] == "Original 27 features" and r["s1_threshold"] == 0.55][0]
    mod_comb = [r for r in all_results if r["feature_set"] == "27 + 14 gap features" and r["s1_threshold"] == 0.55][0]
    delta = mod_comb["committed_acc"] - mod_orig["committed_acc"]
    if delta > 0.005:
        print(f"  Gap features IMPROVE committed accuracy by {delta:+.1%} at moderate threshold")
    elif delta < -0.005:
        print(f"  Gap features HURT committed accuracy by {delta:+.1%} at moderate threshold")
    else:
        print(f"  Gap features have NEGLIGIBLE impact ({delta:+.1%}) at moderate threshold")

    hi_orig = [r for r in all_results if r["feature_set"] == "Original 27 features" and r["s1_threshold"] == 0.70][0]
    hi_comb = [r for r in all_results if r["feature_set"] == "27 + 14 gap features" and r["s1_threshold"] == 0.70][0]
    hi_delta = hi_comb["committed_acc"] - hi_orig["committed_acc"]
    print(f"  At high-confidence (0.70/0.70): {hi_delta:+.1%} delta, "
          f"spread {hi_comb['spread']:.3f} vs {hi_orig['spread']:.3f}")
    print()


if __name__ == "__main__":
    main()
