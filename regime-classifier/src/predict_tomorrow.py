"""Predict Monday March 23, 2026 regime using two-stage model.

Trains on all data up to March 20 (last trading day).
Handles missing GIFT data gracefully (fills with 0).
Shows sensitivity to GIFT gap scenarios.
"""

import numpy as np
import pandas as pd
import psycopg2
from xgboost import XGBClassifier

DB_DSN = "host=localhost dbname=atdb user=me password=algotrix"
CSV_PATH = "/home/me/projects/algotrix-go/regime-classifier/data/preopen_feature_matrix.csv"

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

V2_FEATURES = [
    "range_compression", "bb_width_20d", "range_contraction_streak",
    "vix_percentile", "vix_zscore_20d",
    "momentum_divergence", "momentum_acceleration",
    "volume_trend", "vol_breadth_confirmation",
    "is_monday", "days_to_month_end", "week_of_month",
]


def load_db_data():
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
    """Compute v2 features. Returns full history (not shifted — caller handles)."""
    df = nifty.sort_values("date").reset_index(drop=True)

    df["prev_close"] = df["close"].shift(1)
    df["return_pct"] = df["close"] / df["prev_close"] - 1
    day_range = df["high"] - df["low"]
    df["range_pct"] = day_range / df["close"] * 100

    rolling_range_20 = df["range_pct"].rolling(20).mean()
    df["range_compression"] = df["range_pct"] / rolling_range_20
    df["bb_width_20d"] = df["return_pct"].rolling(20).std() * 2

    below_avg = (df["range_pct"] < rolling_range_20).astype(int)
    streak = np.zeros(len(df))
    for i in range(1, len(df)):
        streak[i] = streak[i - 1] + 1 if below_avg.iloc[i] == 1 else 0
    df["range_contraction_streak"] = streak

    df = df.merge(vix, on="date", how="left")

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

    ret_5d = df["return_pct"].rolling(5).sum()
    ret_20d = df["return_pct"].rolling(20).sum()
    df["momentum_divergence"] = (np.sign(ret_5d) != np.sign(ret_20d)).astype(float)
    df["momentum_acceleration"] = ret_5d - (ret_20d / 4)

    vol_5d = df["volume"].rolling(5).mean()
    vol_20d = df["volume"].rolling(20).mean()
    df["volume_trend"] = vol_5d / vol_20d

    df = df.merge(breadth, on="date", how="left")
    df["vol_breadth_confirmation"] = df["breadth_ratio_raw"].fillna(0.5) * df["volume_trend"]

    df["is_monday"] = (df["date"].dt.dayofweek == 0).astype(float)

    days_to_me = np.zeros(len(df))
    for i in range(len(df)):
        d = df["date"].iloc[i]
        month_end = d + pd.offsets.MonthEnd(0)
        bdays = np.busday_count(d.date(), month_end.date())
        days_to_me[i] = max(bdays, 0)
    df["days_to_month_end"] = days_to_me

    df["week_of_month"] = ((df["date"].dt.day - 1) // 7 + 1).astype(float)

    return df


def build_training_data(fm, v2_full, labels):
    """Merge and shift v2 features for training (use prev day's v2 values)."""
    # Shift v2 features by 1 for training rows
    v2_cols_to_shift = [c for c in V2_FEATURES if c not in ("is_monday", "days_to_month_end", "week_of_month")]
    v2_shifted = v2_full[["date"] + V2_FEATURES].copy()
    for col in v2_cols_to_shift:
        v2_shifted[col] = v2_shifted[col].shift(1)

    df = fm.merge(v2_shifted, on="date", how="left")
    df = df.merge(labels[["date", "coincident_label", "nifty_return"]], on="date", how="left")
    return df


def build_tomorrow_features(fm, v2_full, target_date):
    """Build feature vector for the target prediction date (March 23).

    For v2 features: use March 20's (last trading day) computed values.
    For calendar features: use March 23's values.
    For GIFT features: fill with 0 (not available yet).
    """
    # Get last row from feature matrix (March 20)
    last_fm = fm[fm["date"] <= pd.Timestamp("2026-03-20")].iloc[-1].copy()

    # For original features that are "prev_*", they're already set for the last CSV row.
    # But for March 23 we need to construct the row manually.
    # The CSV's last row (March 20) has prev_* values computed from March 19.
    # For March 23 prediction, we need prev_* values from March 20.

    # Get March 20 data from labels/ground truth
    # Since the CSV might already encode March 20 as the latest row with its own features,
    # we'll use v2_full for March 20 EOD values.

    v2_march20 = v2_full[v2_full["date"] == pd.Timestamp("2026-03-20")]
    if len(v2_march20) == 0:
        print("WARNING: No v2 data for March 20")
        v2_march20 = v2_full.iloc[-1:]

    tomorrow = {}

    # GIFT features: fill with 0 (not available pre-market on prediction day)
    gift_cols = [c for c in ORIG_FEATURES if c.startswith("gift_")]
    for c in gift_cols:
        tomorrow[c] = 0.0

    # Previous day features from March 20 ground truth/v2
    mar20 = v2_march20.iloc[0]
    tomorrow["prev_nifty_return"] = mar20.get("return_pct", 0)

    # 5d and 20d returns from v2_full
    v2_sorted = v2_full.sort_values("date").reset_index(drop=True)
    mar20_idx = v2_sorted[v2_sorted["date"] == pd.Timestamp("2026-03-20")].index
    if len(mar20_idx) > 0:
        idx = mar20_idx[0]
        rets = v2_sorted["return_pct"].iloc[:idx + 1]
        tomorrow["prev_nifty_return_5d"] = rets.iloc[-5:].sum() if len(rets) >= 5 else rets.sum()
        tomorrow["prev_nifty_return_20d"] = rets.iloc[-20:].sum() if len(rets) >= 20 else rets.sum()
    else:
        tomorrow["prev_nifty_return_5d"] = last_fm.get("prev_nifty_return_5d", 0)
        tomorrow["prev_nifty_return_20d"] = last_fm.get("prev_nifty_return_20d", 0)

    tomorrow["prev_vix_close"] = mar20.get("vix_close", last_fm.get("prev_vix_close", 0))

    # VIX change: use March 20 vs March 19
    if len(mar20_idx) > 0:
        idx = mar20_idx[0]
        if idx > 0:
            prev_vix = v2_sorted["vix_close"].iloc[idx - 1]
            cur_vix = v2_sorted["vix_close"].iloc[idx]
            if prev_vix and not np.isnan(prev_vix) and prev_vix != 0:
                tomorrow["prev_vix_change_pct"] = (cur_vix / prev_vix - 1) * 100
            else:
                tomorrow["prev_vix_change_pct"] = 0
        else:
            tomorrow["prev_vix_change_pct"] = 0
    else:
        tomorrow["prev_vix_change_pct"] = 0

    # Use last available values for features we can't easily recompute
    for col in ["prev_ad_ratio", "prev_breadth_turnover_weighted", "prev_pcr_oi",
                "prev_max_pain_distance_pct", "prev_fii_net_idx_fut", "prev_fii_net_total",
                "prev_dii_net_total", "prev_fii_options_skew", "prev_index_divergence_500",
                "prev_index_divergence_midcap", "prev_coincident_regime",
                "sp500_overnight_return", "usdinr_overnight_change"]:
        tomorrow[col] = last_fm.get(col, 0)

    # Calendar features for March 23
    target = pd.Timestamp(target_date)
    tomorrow["day_of_week"] = target.dayofweek
    tomorrow["is_expiry_week"] = last_fm.get("is_expiry_week", 0)
    tomorrow["days_to_monthly_expiry"] = max(0, last_fm.get("days_to_monthly_expiry", 0) - 1)

    # Range pct from March 20
    tomorrow["prev_day_range_pct"] = mar20.get("range_pct", last_fm.get("prev_day_range_pct", 0))

    # coincident regime: use March 20's label (mapped to numeric)
    regime_map = {"Trend-Up": 2, "Range": 1, "Trend-Down": 0}
    # Get from ground truth
    conn = psycopg2.connect(DB_DSN)
    gt = pd.read_sql(
        "SELECT coincident_label FROM regime_ground_truth WHERE date = '2026-03-20'",
        conn,
    )
    conn.close()
    if len(gt) > 0:
        tomorrow["prev_coincident_regime"] = regime_map.get(gt.iloc[0]["coincident_label"], 1)
    else:
        tomorrow["prev_coincident_regime"] = 1

    # v2 features: use March 20's values (already computed in v2_full)
    v2_cols_shift = [c for c in V2_FEATURES if c not in ("is_monday", "days_to_month_end", "week_of_month")]
    for col in v2_cols_shift:
        tomorrow[col] = mar20.get(col, 0)

    # Calendar v2 for March 23
    tomorrow["is_monday"] = 1.0  # March 23, 2026 is Monday
    month_end = target + pd.offsets.MonthEnd(0)
    tomorrow["days_to_month_end"] = max(np.busday_count(target.date(), month_end.date()), 0)
    tomorrow["week_of_month"] = (target.day - 1) // 7 + 1

    return pd.Series(tomorrow)


def train_two_stage(df, feature_cols):
    """Train Stage 1 and Stage 2 models on full training data."""
    valid = df.dropna(subset=["coincident_label"]).copy()

    # Stage 1: Trending (1) vs Range (0)
    valid["_s1"] = valid["coincident_label"].map({"Trend-Up": 1, "Trend-Down": 1, "Range": 0})
    X_s1 = valid[feature_cols].fillna(0).values
    y_s1 = valid["_s1"].astype(int).values

    s1_model = XGBClassifier(
        max_depth=4, n_estimators=200, learning_rate=0.05,
        subsample=0.8, use_label_encoder=False,
        eval_metric="logloss", objective="binary:logistic",
        verbosity=0, random_state=42,
    )
    s1_model.fit(X_s1, y_s1)

    # Stage 2: Up (1) vs Down (0), trending days only
    trending = valid[valid["coincident_label"].isin(["Trend-Up", "Trend-Down"])].copy()
    trending["_s2"] = trending["coincident_label"].map({"Trend-Up": 1, "Trend-Down": 0})
    X_s2 = trending[feature_cols].fillna(0).values
    y_s2 = trending["_s2"].astype(int).values

    s2_model = XGBClassifier(
        max_depth=4, n_estimators=200, learning_rate=0.05,
        subsample=0.8, use_label_encoder=False,
        eval_metric="logloss", objective="binary:logistic",
        verbosity=0, random_state=42,
    )
    s2_model.fit(X_s2, y_s2)

    return s1_model, s2_model


def predict_with_models(s1_model, s2_model, feature_vec, feature_cols):
    """Run two-stage prediction on a single feature vector."""
    X = pd.DataFrame([feature_vec[feature_cols].fillna(0)]).values

    s1_pred = s1_model.predict(X)[0]
    s1_proba = s1_model.predict_proba(X)[0]
    s1_conf = float(s1_proba.max())
    s1_label = "Trending" if s1_pred == 1 else "Range"

    if s1_pred == 0:
        return "Range", s1_conf, s1_label, s1_conf, None, None
    else:
        s2_pred = s2_model.predict(X)[0]
        s2_proba = s2_model.predict_proba(X)[0]
        s2_conf = float(s2_proba.max())
        s2_label = "Trend-Up" if s2_pred == 1 else "Trend-Down"
        combined_conf = min(s1_conf, s2_conf)
        return s2_label, combined_conf, s1_label, s1_conf, s2_label, s2_conf


def main():
    print("=" * 65)
    print("  REGIME PREDICTION: Monday March 23, 2026")
    print("=" * 65)

    print("\nLoading data...")
    fm = pd.read_csv(CSV_PATH, parse_dates=["date"])
    nifty, vix, breadth, labels = load_db_data()

    print("Computing v2 features...")
    v2_full = compute_v2_features(nifty, vix, breadth)

    # Build training data (all up to March 20)
    train_df = build_training_data(fm, v2_full, labels)
    train_df = train_df[train_df["date"] <= pd.Timestamp("2026-03-20")]

    avail_orig = [c for c in ORIG_FEATURES if c in train_df.columns]
    avail_v2 = [c for c in V2_FEATURES if c in train_df.columns]
    all_features = avail_orig + avail_v2

    print(f"  Training rows: {len(train_df.dropna(subset=['coincident_label']))}")
    print(f"  Features: {len(all_features)} ({len(avail_orig)} orig + {len(avail_v2)} v2)")

    # Train models
    print("\nTraining two-stage model on full history...")
    s1_model, s2_model = train_two_stage(train_df, all_features)

    # Build tomorrow's features
    print("Building feature vector for March 23...")
    tomorrow = build_tomorrow_features(fm, v2_full, "2026-03-23")

    # Predict
    regime, conf, s1_label, s1_conf, s2_label, s2_conf = predict_with_models(
        s1_model, s2_model, tomorrow, all_features
    )

    print(f"\n{'='*65}")
    print(f"  PREDICTION: Monday March 23, 2026")
    print(f"{'='*65}")
    print(f"  Predicted regime:  {regime}")
    print(f"  Combined confidence: {conf:.1%}")
    print(f"")
    print(f"  Stage 1 (Trending vs Range): {s1_label} ({s1_conf:.1%})")
    if s2_label:
        print(f"  Stage 2 (Up vs Down):        {s2_label} ({s2_conf:.1%})")
    else:
        print(f"  Stage 2: not triggered (Range prediction)")

    # Key features
    print(f"\n  Key input values:")
    print(f"    GIFT gap:         0.0 (not yet available)")
    print(f"    Prev Nifty return: {tomorrow.get('prev_nifty_return', 0)*100:+.3f}%")
    print(f"    Prev 5d return:    {tomorrow.get('prev_nifty_return_5d', 0)*100:+.3f}%")
    print(f"    Prev 20d return:   {tomorrow.get('prev_nifty_return_20d', 0)*100:+.3f}%")
    print(f"    VIX close:         {tomorrow.get('prev_vix_close', 0):.2f}")
    print(f"    VIX change:        {tomorrow.get('prev_vix_change_pct', 0):+.2f}%")
    print(f"    Range compression: {tomorrow.get('range_compression', 0):.3f}")
    print(f"    VIX percentile:    {tomorrow.get('vix_percentile', 0):.3f}")
    print(f"    Momentum divergence: {tomorrow.get('momentum_divergence', 0):.0f}")
    print(f"    Volume trend:      {tomorrow.get('volume_trend', 0):.3f}")
    print(f"    Day of week:       Monday")
    print(f"    Days to month end: {tomorrow.get('days_to_month_end', 0):.0f}")
    print(f"    Prev regime:       {['Trend-Down','Range','Trend-Up'][int(tomorrow.get('prev_coincident_regime',1))]}")

    # === GIFT Sensitivity Analysis ===
    print(f"\n{'='*65}")
    print(f"  GIFT SENSITIVITY ANALYSIS")
    print(f"{'='*65}")
    print(f"  What changes if GIFT gap is known pre-market?\n")

    scenarios = [
        ("Positive gap (+0.5%)", 0.005, 0.008, 0.02, 0.6, 0.01),
        ("Positive gap (+1.0%)", 0.010, 0.015, 0.05, 0.8, 0.02),
        ("Flat gap (0.0%)",      0.000, 0.003, 0.00, 0.5, 0.00),
        ("Negative gap (-0.5%)", -0.005, 0.008, -0.02, 0.4, -0.01),
        ("Negative gap (-1.0%)", -0.010, 0.015, -0.05, 0.3, -0.02),
    ]

    print(f"  {'Scenario':<25s} {'Regime':<14s} {'Conf':>6s}  {'S1':>10s}  {'S2':>12s}")
    print(f"  {'-'*25} {'-'*14} {'-'*6}  {'-'*10}  {'-'*12}")

    for name, gap, rng, oi_chg, vol_conv, vol_delta in scenarios:
        t = tomorrow.copy()
        t["gift_overnight_gap_pct"] = gap
        t["gift_overnight_range_pct"] = rng
        t["gift_overnight_oi_change_pct"] = oi_chg
        t["gift_overnight_volume_conviction"] = vol_conv
        t["gift_overnight_vol_delta"] = vol_delta

        r, c, s1l, s1c, s2l, s2c = predict_with_models(s1_model, s2_model, t, all_features)
        s2_str = f"{s2l} ({s2c:.0%})" if s2l else "n/a"
        print(f"  {name:<25s} {r:<14s} {c:>5.0%}   {s1l:>8s}   {s2_str:>12s}")

    print()


if __name__ == "__main__":
    main()
