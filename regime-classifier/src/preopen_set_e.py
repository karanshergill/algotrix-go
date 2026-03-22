"""Pre-Open Predictability: E3 (Trend-Up / Range / Trend-Down).

E3 = Percentile-based thresholds using trailing 252-day rolling percentiles.
Replaces old fixed-threshold Set E with adaptive E3 logic.
"""

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, classification_report, confusion_matrix
from xgboost import XGBClassifier

MIN_TRAIN_DAYS = 126
RETRAIN_INTERVAL = 63

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


def label_e3(row):
    """E3 percentile-based: Trend-Up / Range / Trend-Down."""
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


def walk_forward(df, feature_cols, target_col, label_map, n_classes):
    valid = df.dropna(subset=[target_col]).copy().reset_index(drop=True)
    valid["_target"] = valid[target_col].map(label_map)
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
                num_class=n_classes, verbosity=0, random_state=42,
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


def main():
    import psycopg2

    # Load feature matrix
    fm = pd.read_csv(
        "/home/me/projects/algotrix-go/regime-classifier/data/preopen_feature_matrix.csv",
        parse_dates=["date"],
    )

    # Load Nifty OHLCV for E3 label computation
    conn = psycopg2.connect("host=localhost dbname=atdb user=me password=algotrix")
    nifty = pd.read_sql(
        "SELECT date, open, high, low, close FROM nse_indices_daily WHERE index = 'Nifty 50' ORDER BY date",
        conn, parse_dates=["date"],
    )
    breadth = pd.read_sql(
        """SELECT date,
           SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END)::float /
           NULLIF(SUM(CASE WHEN close != prev_close THEN 1 ELSE 0 END), 0) as breadth_ratio
        FROM nse_cm_bhavcopy GROUP BY date ORDER BY date""",
        conn, parse_dates=["date"],
    )
    conn.close()

    # Compute derived features for labeling
    nifty = nifty.sort_values("date").reset_index(drop=True)
    nifty["prev_close"] = nifty["close"].shift(1)
    nifty["return_pct"] = (nifty["close"] / nifty["prev_close"]) - 1
    nifty["day_range"] = nifty["high"] - nifty["low"]
    nifty["cir"] = np.where(nifty["day_range"] == 0, 0.5,
                            (nifty["close"] - nifty["low"]) / nifty["day_range"])
    nifty = nifty.merge(breadth, on="date", how="left")

    # Compute E3 rolling percentiles
    nifty = compute_e3_percentiles(nifty)

    # Apply E3 labels
    nifty["label_e3"] = nifty.apply(label_e3, axis=1)

    # Merge into feature matrix
    fm = fm.merge(nifty[["date", "label_e3"]], on="date", how="left")

    avail = [c for c in FEATURE_COLS if c in fm.columns]
    print(f"Feature matrix: {len(fm)} rows, {len(avail)} features")
    print(f"E3 labels: {fm['label_e3'].value_counts().to_dict()}")

    # --- Set A (old) ---
    label_map_a = {"Bearish": 0, "Neutral": 1, "Bullish": 2}
    inv_a = {0: "Bearish", 1: "Neutral", 2: "Bullish"}
    preds_a, _ = walk_forward(fm, avail, "coincident_truth", label_map_a, 3)

    # --- E3 ---
    label_map_e3 = {"Trend-Down": 0, "Range": 1, "Trend-Up": 2}
    inv_e3 = {0: "Trend-Down", 1: "Range", 2: "Trend-Up"}
    preds_e3, model_e3 = walk_forward(fm, avail, "label_e3", label_map_e3, 3)

    # Evaluate both
    for name, preds, inv in [
        ("Set A (Bull/Neutral/Bear)", preds_a, inv_a),
        ("E3 (Trend-Up/Range/Trend-Down)", preds_e3, inv_e3),
    ]:
        y_true = preds["actual"].values
        y_pred = preds["predicted"].values
        acc = accuracy_score(y_true, y_pred)
        f1 = f1_score(y_true, y_pred, average="macro", zero_division=0)
        most_common = pd.Series(y_true).mode()[0]
        baseline = accuracy_score(y_true, np.full_like(y_true, most_common))
        prev = np.roll(y_true, 1); prev[0] = most_common
        persist = accuracy_score(y_true, prev)

        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")
        print(f"  Accuracy: {acc:.4f} | F1-macro: {f1:.4f} | N={len(preds)}")
        print(f"  Baseline (most common '{inv[most_common]}'): {baseline:.4f}")
        print(f"  Persistence: {persist:.4f}")
        print(f"  Margin over baseline: {acc - baseline:+.4f}")
        print(f"  Margin over persistence: {acc - persist:+.4f}")

        labels = sorted(inv.keys())
        label_names = [inv[l] for l in labels]
        print(classification_report(y_true, y_pred, labels=labels, target_names=label_names, zero_division=0))

        print("  Return separation (by predicted class):")
        for code in labels:
            mask = y_pred == code
            if mask.sum() > 0:
                mean_ret = preds.loc[mask, "nifty_return"].mean() * 100
                print(f"    {inv[code]:>12s}: {mean_ret:+.3f}% (n={mask.sum()})")

        # Confidence
        print("  Confidence analysis:")
        for t in [0.5, 0.6, 0.7]:
            hc = preds[preds["confidence"] >= t]
            if len(hc) > 0:
                hc_acc = accuracy_score(hc["actual"], hc["predicted"])
                print(f"    >= {t:.0%}: {hc_acc:.4f} (n={len(hc)}, {len(hc)/len(preds)*100:.0f}%)")

    # Head-to-head
    acc_a = accuracy_score(preds_a["actual"], preds_a["predicted"])
    acc_e3 = accuracy_score(preds_e3["actual"], preds_e3["predicted"])
    f1_a = f1_score(preds_a["actual"], preds_a["predicted"], average="macro", zero_division=0)
    f1_e3 = f1_score(preds_e3["actual"], preds_e3["predicted"], average="macro", zero_division=0)

    base_a = accuracy_score(preds_a["actual"], np.full(len(preds_a), pd.Series(preds_a["actual"]).mode()[0]))
    base_e3 = accuracy_score(preds_e3["actual"], np.full(len(preds_e3), pd.Series(preds_e3["actual"]).mode()[0]))

    print(f"\n{'='*60}")
    print(f"  HEAD-TO-HEAD: Set A vs E3")
    print(f"{'='*60}")
    print(f"  {'Metric':<30s} {'Set A':>10s} {'E3':>10s} {'Delta':>10s}")
    print(f"  {'-'*30} {'-'*10} {'-'*10} {'-'*10}")
    print(f"  {'Accuracy':<30s} {acc_a:>10.4f} {acc_e3:>10.4f} {acc_e3-acc_a:>+10.4f}")
    print(f"  {'F1-macro':<30s} {f1_a:>10.4f} {f1_e3:>10.4f} {f1_e3-f1_a:>+10.4f}")
    print(f"  {'Baseline (most common)':<30s} {base_a:>10.4f} {base_e3:>10.4f}")
    print(f"  {'Margin over baseline':<30s} {acc_a-base_a:>+10.4f} {acc_e3-base_e3:>+10.4f}")

    # Feature importance
    if hasattr(model_e3, 'feature_importances_'):
        print(f"\n  Top 10 features (E3):")
        imp = sorted(zip(avail, model_e3.feature_importances_), key=lambda x: -x[1])
        for feat, score in imp[:10]:
            print(f"    {feat:40s} {score:.4f}")


if __name__ == "__main__":
    main()
