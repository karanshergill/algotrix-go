"""Backfill regime_ground_truth with E3 percentile-based labels.

Steps:
1. Backup existing regime_ground_truth to CSV
2. Load raw data from DB (same as calibrate_labels.py)
3. Compute E3 labels for all rows
4. ALTER TABLE to add cir/range_pct columns if needed
5. UPDATE coincident_label and predictive_label with new labels
6. Print summary of changes
"""

import os
import numpy as np
import pandas as pd
import psycopg2

DB_DSN = "host=localhost dbname=atdb user=me password=algotrix"
DATE_START = "2020-01-02"
DATE_END = "2026-03-20"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
BACKUP_CSV = os.path.join(DATA_DIR, "regime_gt_backup_set_a.csv")


def load_raw_data(conn):
    """Load raw data from DB tables (same approach as calibrate_labels.py)."""
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

    return nifty, vix, breadth


def compute_features(nifty, vix, breadth):
    """Compute derived features (matches calibrate_labels.py)."""
    df = nifty.sort_values("date").reset_index(drop=True)

    df["prev_close"] = df["close"].shift(1)
    df["return_pct"] = (df["close"] / df["prev_close"]) - 1

    day_range = df["high"] - df["low"]
    df["cir"] = np.where(day_range == 0, 0.5, (df["close"] - df["low"]) / day_range)
    df["range_pct"] = day_range / df["close"] * 100

    df = df.merge(breadth, on="date", how="left")

    vix = vix.sort_values("date").reset_index(drop=True)
    vix_df = vix[["date", "close"]].rename(columns={"close": "vix_level"})
    df = df.merge(vix_df, on="date", how="left")

    return df


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
    """E3 percentile-based label (matches calibrate_labels.py exactly)."""
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


def remap_predictive(old_label):
    """Map old predictive labels to new names."""
    mapping = {"Bullish": "Trend-Up", "Neutral": "Range", "Bearish": "Trend-Down"}
    return mapping.get(old_label, old_label)


def main():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    # ── Step 1: Backup ────────────────────────────────────────────────────
    print("Backing up regime_ground_truth...")
    backup = pd.read_sql("SELECT * FROM regime_ground_truth ORDER BY date", conn)
    os.makedirs(DATA_DIR, exist_ok=True)
    backup.to_csv(BACKUP_CSV, index=False)
    print(f"  Saved {len(backup)} rows to {BACKUP_CSV}")

    old_coincident = backup["coincident_label"].value_counts()
    old_predictive = backup["predictive_label"].value_counts()
    print(f"\n  Old coincident distribution:\n{old_coincident.to_string()}")
    print(f"\n  Old predictive distribution:\n{old_predictive.to_string()}")

    # ── Step 2: Load raw data & compute E3 labels ─────────────────────────
    print("\nLoading raw data...")
    nifty, vix, breadth = load_raw_data(conn)
    print(f"  Nifty: {len(nifty)} rows, VIX: {len(vix)} rows, Breadth: {len(breadth)} rows")

    print("Computing features...")
    df = compute_features(nifty, vix, breadth)

    print("Computing E3 rolling percentiles...")
    df = compute_e3_percentiles(df)

    print("Applying E3 labels...")
    df["label_e3"] = df.apply(label_e3, axis=1)

    valid = df.dropna(subset=["label_e3"])
    print(f"  E3 labels computed: {len(valid)} rows")
    print(f"  E3 distribution:\n{valid['label_e3'].value_counts().to_string()}")

    # ── Step 3: ALTER TABLE to add cir/range_pct if needed ────────────────
    print("\nAdding cir/range_pct columns if needed...")
    for col, coltype in [("cir", "REAL"), ("range_pct", "REAL")]:
        cur.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name = 'regime_ground_truth' AND column_name = %s""",
            (col,),
        )
        if cur.fetchone() is None:
            cur.execute(f"ALTER TABLE regime_ground_truth ADD COLUMN {col} {coltype}")
            print(f"  Added column: {col} {coltype}")
        else:
            print(f"  Column {col} already exists")
    conn.commit()

    # ── Step 4: UPDATE coincident_label, cir, range_pct ───────────────────
    print("\nUpdating coincident_label with E3 labels...")
    update_count = 0
    for _, row in valid.iterrows():
        date = row["date"]
        label = row["label_e3"]
        cir_val = float(row["cir"]) if not pd.isna(row["cir"]) else None
        range_pct_val = float(row["range_pct"]) if not pd.isna(row["range_pct"]) else None

        cur.execute(
            """UPDATE regime_ground_truth
               SET coincident_label = %s, cir = %s, range_pct = %s
               WHERE date = %s""",
            (label, cir_val, range_pct_val, date),
        )
        update_count += cur.rowcount

    print(f"  Updated {update_count} rows (coincident_label + cir + range_pct)")

    # ── Step 5: UPDATE predictive_label ───────────────────────────────────
    print("Updating predictive_label with new names...")
    for old, new in [("Bullish", "Trend-Up"), ("Neutral", "Range"), ("Bearish", "Trend-Down")]:
        cur.execute(
            "UPDATE regime_ground_truth SET predictive_label = %s WHERE predictive_label = %s",
            (new, old),
        )
        print(f"  {old} → {new}: {cur.rowcount} rows")

    conn.commit()

    # ── Step 6: Verify ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)

    result = pd.read_sql(
        """SELECT coincident_label, COUNT(*),
                  ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER() * 100, 1) as pct
           FROM regime_ground_truth
           GROUP BY coincident_label ORDER BY COUNT(*) DESC""",
        conn,
    )
    print("\nCoincident label distribution (after backfill):")
    for _, row in result.iterrows():
        print(f"  {row['coincident_label']:>12s}: {row['count']:5d}  ({row['pct']}%)")

    result_pred = pd.read_sql(
        """SELECT predictive_label, COUNT(*),
                  ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER() * 100, 1) as pct
           FROM regime_ground_truth
           WHERE predictive_label IS NOT NULL
           GROUP BY predictive_label ORDER BY COUNT(*) DESC""",
        conn,
    )
    print("\nPredictive label distribution (after backfill):")
    for _, row in result_pred.iterrows():
        print(f"  {row['predictive_label']:>12s}: {row['count']:5d}  ({row['pct']}%)")

    # Compare old vs new
    print("\n" + "=" * 60)
    print("CHANGE SUMMARY")
    print("=" * 60)

    new_gt = pd.read_sql("SELECT date, coincident_label FROM regime_ground_truth ORDER BY date", conn)
    merged = backup[["date", "coincident_label"]].merge(
        new_gt, on="date", suffixes=("_old", "_new"),
    )
    changed = merged[merged["coincident_label_old"] != merged["coincident_label_new"]]
    print(f"  Total rows: {len(merged)}")
    print(f"  Changed: {len(changed)} ({len(changed)/len(merged)*100:.1f}%)")
    print(f"  Unchanged: {len(merged) - len(changed)}")

    if len(changed) > 0:
        print("\n  Change breakdown:")
        change_pairs = changed.apply(
            lambda r: f"{r['coincident_label_old']} → {r['coincident_label_new']}", axis=1
        )
        for pair, count in change_pairs.value_counts().items():
            print(f"    {pair}: {count}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
