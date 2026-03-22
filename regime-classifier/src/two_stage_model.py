"""Two-Stage Walk-Forward Regime Predictor with v2 Features.

Stage 1: Trending vs Range (binary)
Stage 2: Up vs Down (binary, conditional on Stage 1 = Trending)
Combined: 3-class output (Trend-Up / Range / Trend-Down)

Compares against single-stage E3 baseline.
"""

import numpy as np
import pandas as pd
import psycopg2
from sklearn.metrics import accuracy_score, f1_score, classification_report
from xgboost import XGBClassifier

DB_DSN = "host=localhost dbname=atdb user=me password=algotrix"
CSV_PATH = "/home/me/projects/algotrix-go/regime-classifier/data/preopen_feature_matrix.csv"

MIN_TRAIN_DAYS = 126
RETRAIN_INTERVAL = 63

# Original 27 features from pre-open CSV
ORIG_FEATURES = [
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

# New v2 features
V2_FEATURES = [
    "range_compression", "bb_width_20d", "range_contraction_streak",
    "vix_percentile", "vix_zscore_20d",
    "momentum_divergence", "momentum_acceleration",
    "volume_trend", "vol_breadth_confirmation",
    "is_monday", "days_to_month_end", "week_of_month",
]


def load_db_data():
    """Load raw data from DB for v2 feature computation."""
    conn = psycopg2.connect(DB_DSN)

    nifty = pd.read_sql(
        "SELECT date, open, high, low, close, volume FROM nse_indices_daily "
        "WHERE index = 'Nifty 50' ORDER BY date",
        conn, parse_dates=["date"],
    )

    vix = pd.read_sql(
        "SELECT date, close as vix_close FROM nse_vix_daily ORDER BY date",
        conn, parse_dates=["date"],
    )

    breadth = pd.read_sql(
        """SELECT date,
           SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END)::float /
           NULLIF(SUM(CASE WHEN close != prev_close THEN 1 ELSE 0 END), 0) as breadth_ratio_raw
        FROM nse_cm_bhavcopy GROUP BY date ORDER BY date""",
        conn, parse_dates=["date"],
    )

    labels = pd.read_sql(
        "SELECT date, coincident_label, nifty_return FROM regime_ground_truth ORDER BY date",
        conn, parse_dates=["date"],
    )

    conn.close()
    return nifty, vix, breadth, labels


def compute_v2_features(nifty, vix, breadth):
    """Compute v2 feature set from raw DB data."""
    df = nifty.sort_values("date").reset_index(drop=True)

    df["prev_close"] = df["close"].shift(1)
    df["return_pct"] = df["close"] / df["prev_close"] - 1
    day_range = df["high"] - df["low"]
    df["range_pct"] = day_range / df["close"] * 100

    # --- Compression/Tension ---
    rolling_range_20 = df["range_pct"].rolling(20).mean()
    df["range_compression"] = df["range_pct"] / rolling_range_20

    df["bb_width_20d"] = df["return_pct"].rolling(20).std() * 2

    # Range contraction streak
    below_avg = (df["range_pct"] < rolling_range_20).astype(int)
    streak = np.zeros(len(df))
    for i in range(1, len(df)):
        if below_avg.iloc[i] == 1:
            streak[i] = streak[i - 1] + 1
        else:
            streak[i] = 0
    df["range_contraction_streak"] = streak

    # --- VIX Regime ---
    df = df.merge(vix, on="date", how="left")

    # VIX percentile rank (trailing 252 days)
    vix_vals = df["vix_close"].values
    vix_pct = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        start = max(0, i - 252)
        window = vix_vals[start:i]
        valid = window[~np.isnan(window)]
        if len(valid) > 0:
            vix_pct[i] = np.mean(valid <= vix_vals[i])
    df["vix_percentile"] = vix_pct

    vix_mean_20 = df["vix_close"].rolling(20).mean()
    vix_std_20 = df["vix_close"].rolling(20).std()
    df["vix_zscore_20d"] = (df["vix_close"] - vix_mean_20) / vix_std_20

    # --- Momentum Divergence ---
    ret_5d = df["return_pct"].rolling(5).sum()
    ret_20d = df["return_pct"].rolling(20).sum()
    df["momentum_divergence"] = (np.sign(ret_5d) != np.sign(ret_20d)).astype(float)
    df["momentum_acceleration"] = ret_5d - (ret_20d / 4)

    # --- Volume Profile ---
    vol_5d = df["volume"].rolling(5).mean()
    vol_20d = df["volume"].rolling(20).mean()
    df["volume_trend"] = vol_5d / vol_20d

    df = df.merge(breadth, on="date", how="left")
    df["vol_breadth_confirmation"] = df["breadth_ratio_raw"].fillna(0.5) * df["volume_trend"]

    # --- Calendar Enhanced ---
    df["is_monday"] = (df["date"].dt.dayofweek == 0).astype(float)

    # Business days to month end
    days_to_me = np.zeros(len(df))
    for i in range(len(df)):
        d = df["date"].iloc[i]
        month_end = d + pd.offsets.MonthEnd(0)
        bdays = np.busday_count(d.date(), month_end.date())
        days_to_me[i] = max(bdays, 0)
    df["days_to_month_end"] = days_to_me

    df["week_of_month"] = ((df["date"].dt.day - 1) // 7 + 1).astype(float)

    # Shift all v2 features by 1 day (use previous day's values for prediction)
    for col in V2_FEATURES:
        if col not in ("is_monday", "days_to_month_end", "week_of_month"):
            df[col] = df[col].shift(1)

    return df[["date"] + V2_FEATURES]


def walk_forward_single_stage(df, feature_cols, label_map, inv_map):
    """Single-stage 3-class walk-forward (E3 baseline)."""
    valid = df.dropna(subset=["coincident_label"]).copy().reset_index(drop=True)
    valid["_target"] = valid["coincident_label"].map(label_map)
    valid = valid.dropna(subset=["_target"]).reset_index(drop=True)

    results = []
    model = None
    last_train_end = -1

    for i in range(MIN_TRAIN_DAYS, len(valid)):
        if model is None or (i - last_train_end) >= RETRAIN_INTERVAL:
            train = valid.iloc[:i]
            X_train = train[feature_cols].fillna(0).values
            y_train = train["_target"].astype(int).values
            model = XGBClassifier(
                max_depth=4, n_estimators=200, learning_rate=0.05,
                subsample=0.8, use_label_encoder=False,
                eval_metric="mlogloss", objective="multi:softprob",
                num_class=3, verbosity=0, random_state=42,
            )
            model.fit(X_train, y_train)
            last_train_end = i

        row = valid.iloc[i]
        X_test = pd.DataFrame([row[feature_cols].fillna(0)]).values
        pred = model.predict(X_test)[0]
        proba = model.predict_proba(X_test)[0]

        results.append({
            "date": row["date"],
            "actual": int(row["_target"]),
            "predicted": int(pred),
            "confidence": float(proba.max()),
            "nifty_return": row.get("nifty_return"),
        })

    return pd.DataFrame(results), model


def walk_forward_two_stage(df, feature_cols):
    """Two-stage walk-forward: Stage 1 (Trending/Range) + Stage 2 (Up/Down)."""
    valid = df.dropna(subset=["coincident_label"]).copy().reset_index(drop=True)

    # Stage 1 labels: Trending (1) vs Range (0)
    valid["_s1_target"] = valid["coincident_label"].map(
        {"Trend-Up": 1, "Trend-Down": 1, "Range": 0}
    )
    # Stage 2 labels: Up (1) vs Down (0) — only for trending days
    valid["_s2_target"] = valid["coincident_label"].map(
        {"Trend-Up": 1, "Trend-Down": 0}
    )

    valid = valid.dropna(subset=["_s1_target"]).reset_index(drop=True)

    results = []
    s1_model = None
    s2_model = None
    s1_last_train = -1
    s2_last_train = -1

    # 3-class label for evaluation
    label_3c = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}

    for i in range(MIN_TRAIN_DAYS, len(valid)):
        # --- Stage 1: Trending vs Range ---
        if s1_model is None or (i - s1_last_train) >= RETRAIN_INTERVAL:
            train = valid.iloc[:i]
            X_train = train[feature_cols].fillna(0).values
            y_train = train["_s1_target"].astype(int).values
            s1_model = XGBClassifier(
                max_depth=4, n_estimators=200, learning_rate=0.05,
                subsample=0.8, use_label_encoder=False,
                eval_metric="logloss", objective="binary:logistic",
                verbosity=0, random_state=42,
            )
            s1_model.fit(X_train, y_train)
            s1_last_train = i

        # --- Stage 2: Up vs Down (only trending days in training) ---
        if s2_model is None or (i - s2_last_train) >= RETRAIN_INTERVAL:
            train = valid.iloc[:i]
            trending_mask = train["_s2_target"].notna()
            if trending_mask.sum() >= 30:
                train_trending = train[trending_mask]
                X_train_s2 = train_trending[feature_cols].fillna(0).values
                y_train_s2 = train_trending["_s2_target"].astype(int).values
                s2_model = XGBClassifier(
                    max_depth=4, n_estimators=200, learning_rate=0.05,
                    subsample=0.8, use_label_encoder=False,
                    eval_metric="logloss", objective="binary:logistic",
                    verbosity=0, random_state=42,
                )
                s2_model.fit(X_train_s2, y_train_s2)
                s2_last_train = i

        row = valid.iloc[i]
        X_test = pd.DataFrame([row[feature_cols].fillna(0)]).values

        s1_pred = s1_model.predict(X_test)[0]
        s1_proba = s1_model.predict_proba(X_test)[0]
        s1_conf = float(s1_proba.max())

        if s1_pred == 0:
            # Range
            combined_pred = 1  # Range in 3-class
            combined_conf = s1_conf
        else:
            # Trending → run Stage 2
            if s2_model is not None:
                s2_pred = s2_model.predict(X_test)[0]
                s2_proba = s2_model.predict_proba(X_test)[0]
                s2_conf = float(s2_proba.max())
                combined_pred = 2 if s2_pred == 1 else 0  # Trend-Up=2, Trend-Down=0
                combined_conf = min(s1_conf, s2_conf)
            else:
                # Fallback: no S2 model yet, predict Range
                combined_pred = 1
                combined_conf = s1_conf

        actual_3c = label_3c.get(row["coincident_label"])
        if actual_3c is None:
            continue

        results.append({
            "date": row["date"],
            "actual": actual_3c,
            "predicted": combined_pred,
            "confidence": combined_conf,
            "s1_pred": "Trending" if s1_pred == 1 else "Range",
            "s1_conf": s1_conf,
            "nifty_return": row.get("nifty_return"),
        })

    return pd.DataFrame(results), s1_model, s2_model


def print_report(name, preds, inv_map):
    """Print evaluation report for a model."""
    y_true = preds["actual"].values
    y_pred = preds["predicted"].values
    acc = accuracy_score(y_true, y_pred)
    f1 = f1_score(y_true, y_pred, average="macro", zero_division=0)
    most_common = pd.Series(y_true).mode()[0]
    baseline = accuracy_score(y_true, np.full_like(y_true, most_common))
    prev = np.roll(y_true, 1)
    prev[0] = most_common
    persist = accuracy_score(y_true, prev)

    print(f"\n{'='*65}")
    print(f"  {name}")
    print(f"{'='*65}")
    print(f"  Accuracy: {acc:.4f} | F1-macro: {f1:.4f} | N={len(preds)}")
    print(f"  Baseline (most common '{inv_map[most_common]}'): {baseline:.4f}")
    print(f"  Persistence: {persist:.4f}")
    print(f"  Margin over baseline: {acc - baseline:+.4f}")
    print(f"  Margin over persistence: {acc - persist:+.4f}")

    labels = sorted(inv_map.keys())
    label_names = [inv_map[l] for l in labels]
    print(classification_report(
        y_true, y_pred, labels=labels, target_names=label_names, zero_division=0
    ))

    print("  Return separation (by predicted class):")
    for code in labels:
        mask = y_pred == code
        if mask.sum() > 0:
            mean_ret = preds.loc[mask, "nifty_return"].mean() * 100
            print(f"    {inv_map[code]:>12s}: {mean_ret:+.3f}% (n={mask.sum()})")

    print("\n  Confidence analysis:")
    for t in [0.5, 0.6, 0.7]:
        hc = preds[preds["confidence"] >= t]
        if len(hc) > 0:
            hc_acc = accuracy_score(hc["actual"], hc["predicted"])
            print(f"    >= {t:.0%}: {hc_acc:.4f} (n={len(hc)}, {len(hc)/len(preds)*100:.0f}%)")

    return acc, f1, baseline


def main():
    print("Loading data...")

    # Load pre-open feature matrix
    fm = pd.read_csv(CSV_PATH, parse_dates=["date"])

    # Load DB data for v2 features and labels
    nifty, vix, breadth, labels = load_db_data()

    print("Computing v2 features...")
    v2 = compute_v2_features(nifty, vix, breadth)

    # Merge: feature matrix + v2 features + ground truth labels
    df = fm.merge(v2, on="date", how="left")
    # Drop nifty_return from fm if present (will use ground truth version)
    if "nifty_return" in df.columns:
        df = df.drop(columns=["nifty_return"])
    df = df.merge(labels[["date", "coincident_label", "nifty_return"]], on="date", how="left")

    avail_orig = [c for c in ORIG_FEATURES if c in df.columns]
    avail_v2 = [c for c in V2_FEATURES if c in df.columns]
    all_features = avail_orig + avail_v2

    print(f"  Rows: {len(df)} | Original features: {len(avail_orig)} | v2 features: {len(avail_v2)}")
    print(f"  Labels: {df['coincident_label'].value_counts().to_dict()}")

    inv_map = {0: "Trend-Down", 1: "Range", 2: "Trend-Up"}
    label_map = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}

    # === Single-stage E3 baseline (original features only) ===
    print("\nRunning single-stage E3 baseline (original features)...")
    preds_base, _ = walk_forward_single_stage(df, avail_orig, label_map, inv_map)
    acc_base, f1_base, bl_base = print_report(
        "BASELINE: Single-Stage E3 (27 original features)", preds_base, inv_map
    )

    # === Single-stage with ALL features ===
    print("\nRunning single-stage with ALL features (orig + v2)...")
    preds_all, _ = walk_forward_single_stage(df, all_features, label_map, inv_map)
    acc_all, f1_all, bl_all = print_report(
        "Single-Stage E3 (27 orig + 12 v2 features)", preds_all, inv_map
    )

    # === Two-stage with ALL features ===
    print("\nRunning two-stage model (orig + v2 features)...")
    preds_2s, s1_model, s2_model = walk_forward_two_stage(df, all_features)
    acc_2s, f1_2s, bl_2s = print_report(
        "TWO-STAGE (27 orig + 12 v2 features)", preds_2s, inv_map
    )

    # === Two-stage Stage 1 accuracy ===
    if "s1_pred" in preds_2s.columns:
        s1_actual = preds_2s["actual"].map({0: "Trending", 1: "Range", 2: "Trending"})
        s1_pred = preds_2s["s1_pred"]
        s1_acc = accuracy_score(s1_actual, s1_pred)
        print(f"\n{'='*65}")
        print(f"  Stage 1 (Trending vs Range) accuracy: {s1_acc:.4f}")
        s1_majority = s1_actual.mode()[0]
        s1_baseline = (s1_actual == s1_majority).mean()
        print(f"  Stage 1 baseline ({s1_majority}): {s1_baseline:.4f}")
        print(f"  Stage 1 margin: {s1_acc - s1_baseline:+.4f}")

    # === Head-to-Head ===
    print(f"\n{'='*65}")
    print(f"  HEAD-TO-HEAD COMPARISON")
    print(f"{'='*65}")
    print(f"  {'Model':<45s} {'Acc':>7s} {'F1':>7s} {'Margin':>8s}")
    print(f"  {'-'*45} {'-'*7} {'-'*7} {'-'*8}")
    print(f"  {'Single-stage (27 orig)':<45s} {acc_base:>7.4f} {f1_base:>7.4f} {acc_base-bl_base:>+8.4f}")
    print(f"  {'Single-stage (orig + v2)':<45s} {acc_all:>7.4f} {f1_all:>7.4f} {acc_all-bl_all:>+8.4f}")
    print(f"  {'Two-stage (orig + v2)':<45s} {acc_2s:>7.4f} {f1_2s:>7.4f} {acc_2s-bl_2s:>+8.4f}")
    print()

    # Feature importance from final two-stage models
    if s1_model is not None and hasattr(s1_model, 'feature_importances_'):
        print(f"  Top 10 features — Stage 1 (Trending vs Range):")
        imp = sorted(zip(all_features, s1_model.feature_importances_), key=lambda x: -x[1])
        for feat, score in imp[:10]:
            print(f"    {feat:40s} {score:.4f}")

    if s2_model is not None and hasattr(s2_model, 'feature_importances_'):
        print(f"\n  Top 10 features — Stage 2 (Up vs Down):")
        imp = sorted(zip(all_features, s2_model.feature_importances_), key=lambda x: -x[1])
        for feat, score in imp[:10]:
            print(f"    {feat:40s} {score:.4f}")


if __name__ == "__main__":
    main()
