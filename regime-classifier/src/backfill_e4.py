"""Backfill E4 multi-dimensional ground truth labels.

Steps:
1. Load raw data from DB (nse_cm_bhavcopy, nse_indices_daily)
2. Compute E3 labels (reusing backfill_e3 logic)
3. Precompute rolling series for D3–D6 (vectorised, not per-day DB calls)
4. Compute E4 labels for all 3 thresholds: strict (>=3), moderate (>=2), loose (>=0)
5. ALTER TABLE regime_ground_truth to add new columns
6. UPDATE rows with E4 labels + dimension scores
7. Export to regime-classifier/data/e4_labels.csv
8. Print summary stats
"""

import os
import sys
import numpy as np
import pandas as pd
import psycopg2

from src.ground_truth_e4 import (
    SECTOR_INDICES, ROLLING_WINDOW, compute_e4_label,
)

DB_DSN = "host=localhost dbname=atdb user=me password=algotrix"
DATE_START = "2020-01-02"
DATE_END = "2026-03-23"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_all_data(conn):
    """Load all required tables into DataFrames."""
    print("Loading nse_indices_daily (Nifty 50)...")
    nifty = pd.read_sql(
        """SELECT date, open, high, low, close
           FROM nse_indices_daily WHERE index = 'Nifty 50'
           AND date BETWEEN %s AND %s ORDER BY date""",
        conn, params=(DATE_START, DATE_END), parse_dates=["date"],
    )
    print(f"  {len(nifty)} rows")

    print("Loading nse_cm_bhavcopy...")
    cm = pd.read_sql(
        """SELECT isin, date, close, prev_close, volume, traded_value, num_trades
           FROM nse_cm_bhavcopy
           WHERE date BETWEEN %s AND %s ORDER BY date, isin""",
        conn, params=(DATE_START, DATE_END), parse_dates=["date"],
    )
    print(f"  {len(cm)} rows")

    print("Loading nse_indices_daily (sector indices)...")
    sector_list = "', '".join(SECTOR_INDICES)
    indices = pd.read_sql(
        f"""SELECT index, date, close
            FROM nse_indices_daily
            WHERE index IN ('{sector_list}')
              AND date BETWEEN %s AND %s
            ORDER BY date, index""",
        conn, params=(DATE_START, DATE_END), parse_dates=["date"],
    )
    print(f"  {len(indices)} rows across {indices['index'].nunique()} sector indices")

    print("Loading nse_vix_daily...")
    vix = pd.read_sql(
        """SELECT date, close
           FROM nse_vix_daily
           WHERE date BETWEEN %s AND %s ORDER BY date""",
        conn, params=(DATE_START, DATE_END), parse_dates=["date"],
    )
    print(f"  {len(vix)} rows")

    print("Loading breadth from nse_cm_bhavcopy...")
    breadth = pd.read_sql(
        """SELECT date,
           SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END)::float /
           NULLIF(SUM(CASE WHEN close != prev_close THEN 1 ELSE 0 END), 0) as breadth_ratio
           FROM nse_cm_bhavcopy
           WHERE date BETWEEN %s AND %s
           GROUP BY date ORDER BY date""",
        conn, params=(DATE_START, DATE_END), parse_dates=["date"],
    )
    print(f"  {len(breadth)} rows")

    return nifty, cm, indices, vix, breadth


# ---------------------------------------------------------------------------
# E3 label computation (reused from backfill_e3)
# ---------------------------------------------------------------------------

def compute_e3_labels(nifty, vix, breadth):
    """Compute E3 labels for all days (same logic as backfill_e3.py)."""
    df = nifty.sort_values("date").reset_index(drop=True)
    df["prev_close"] = df["close"].shift(1)
    df["return_pct"] = (df["close"] / df["prev_close"]) - 1

    day_range = df["high"] - df["low"]
    df["cir"] = np.where(day_range == 0, 0.5, (df["close"] - df["low"]) / day_range)
    df = df.merge(breadth, on="date", how="left")

    # Rolling 252-day percentiles
    n = len(df)
    abs_ret = df["return_pct"].abs()
    for col in ["ret_p33", "ret_p67", "cir_p33", "cir_p67", "breadth_p33", "breadth_p67"]:
        df[col] = np.nan

    for i in range(1, n):
        start = max(0, i - 252)
        win = slice(start, i)

        ret_vals = abs_ret.iloc[win].dropna()
        if len(ret_vals) > 0:
            df.loc[df.index[i], "ret_p33"] = np.percentile(ret_vals, 33)
            df.loc[df.index[i], "ret_p67"] = np.percentile(ret_vals, 67)

        cir_vals = df["cir"].iloc[win].dropna()
        if len(cir_vals) > 0:
            df.loc[df.index[i], "cir_p33"] = np.percentile(cir_vals, 33)
            df.loc[df.index[i], "cir_p67"] = np.percentile(cir_vals, 67)

        br_vals = df["breadth_ratio"].iloc[win].dropna()
        if len(br_vals) > 0:
            df.loc[df.index[i], "breadth_p33"] = np.percentile(br_vals, 33)
            df.loc[df.index[i], "breadth_p67"] = np.percentile(br_vals, 67)

    # Apply E3 label logic
    def _label_e3(row):
        ret = row["return_pct"]
        cir = row["cir"]
        br = row["breadth_ratio"]

        if pd.isna(ret) or pd.isna(cir):
            return None

        ret_p33, ret_p67 = row["ret_p33"], row["ret_p67"]
        cir_p33, cir_p67 = row["cir_p33"], row["cir_p67"]
        breadth_p33, breadth_p67 = row["breadth_p33"], row["breadth_p67"]

        if ret > ret_p67 and cir > cir_p67 and (pd.isna(br) or br > breadth_p67):
            return "Trend-Up"
        if ret < -ret_p67 and cir < cir_p33 and (pd.isna(br) or br < breadth_p33):
            return "Trend-Down"

        cir_mid = (cir_p33 + cir_p67) / 2
        if ret > ret_p33 and cir > cir_mid:
            return "Trend-Up"
        if ret < -ret_p33 and cir < cir_mid:
            return "Trend-Down"

        return "Range"

    df["label_e3"] = df.apply(_label_e3, axis=1)
    return df


# ---------------------------------------------------------------------------
# Vectorised D3–D6 computation (avoids per-day groupby)
# ---------------------------------------------------------------------------

def compute_all_dimensions(trading_dates, cm, indices, nifty_df):
    """Compute D3–D6 scores for all trading dates in a vectorised manner.

    Returns a DataFrame indexed by date with columns:
        d3_score, d3_raw, d4_score, d4_raw, d5_score, d5_raw,
        d6_score, d6_raw, d6_sectors_agreeing
    """
    print("\nPrecomputing D3 (volume conviction)...")
    daily_turnover = cm.groupby("date")["traded_value"].sum().sort_index()
    turnover_20d_avg = daily_turnover.rolling(ROLLING_WINDOW, min_periods=1).mean().shift(1)
    d3_raw = (daily_turnover / turnover_20d_avg).rename("d3_raw")
    d3_score = pd.Series(0, index=d3_raw.index, name="d3_score")
    d3_score[d3_raw >= 1.20] = 1
    d3_score[d3_raw <= 0.80] = -1
    d3_score[d3_raw.isna()] = 0

    print("Precomputing D4 (cross-sectional dispersion)...")
    cm_valid = cm[cm["prev_close"] > 0].copy()
    cm_valid["stock_return"] = (cm_valid["close"] - cm_valid["prev_close"]) / cm_valid["prev_close"]
    daily_disp = cm_valid.groupby("date")["stock_return"].std().sort_index()
    disp_20d_avg = daily_disp.rolling(ROLLING_WINDOW, min_periods=1).mean().shift(1)
    d4_raw = (daily_disp / disp_20d_avg).rename("d4_raw")
    d4_score = pd.Series(0, index=d4_raw.index, name="d4_score")
    d4_score[d4_raw <= 0.85] = 1    # low dispersion confirms trend
    d4_score[d4_raw >= 1.30] = -1   # high dispersion contradicts trend
    d4_score[d4_raw.isna()] = 0

    print("Precomputing D5 (turnover concentration)...")
    def _top10_share(group):
        vals = group.nlargest(10, "traded_value")["traded_value"]
        total = group["traded_value"].sum()
        if total == 0:
            return np.nan
        return vals.sum() / total

    daily_conc = cm.groupby("date").apply(_top10_share).sort_index()
    conc_20d_avg = daily_conc.rolling(ROLLING_WINDOW, min_periods=1).mean().shift(1)
    d5_raw = (daily_conc / conc_20d_avg).rename("d5_raw")
    d5_score = pd.Series(0, index=d5_raw.index, name="d5_score")
    d5_score[d5_raw >= 1.15] = -1   # concentrated
    d5_score[d5_raw <= 0.90] = 1    # distributed
    d5_score[d5_raw.isna()] = 0

    print("Precomputing D6 (sector participation)...")
    # Compute sector returns
    sector_df = indices.copy()
    sector_df = sector_df.sort_values(["index", "date"])
    sector_df["prev_close"] = sector_df.groupby("index")["close"].shift(1)
    sector_df["sector_return"] = (sector_df["close"] - sector_df["prev_close"]) / sector_df["prev_close"]

    # Nifty returns for direction
    nifty_returns = nifty_df.set_index("date")["return_pct"]

    # For each date, count how many sectors agree with nifty direction
    d6_raw_dict = {}
    d6_score_dict = {}
    d6_agreeing_dict = {}

    sector_pivot = sector_df.pivot_table(index="date", columns="index", values="sector_return")

    for dt in trading_dates:
        nifty_ret = nifty_returns.get(dt, np.nan)
        if pd.isna(nifty_ret) or nifty_ret == 0:
            d6_raw_dict[dt] = np.nan
            d6_score_dict[dt] = 0
            d6_agreeing_dict[dt] = 0
            continue

        nifty_dir = np.sign(nifty_ret)

        if dt not in sector_pivot.index:
            d6_raw_dict[dt] = np.nan
            d6_score_dict[dt] = 0
            d6_agreeing_dict[dt] = 0
            continue

        sector_rets = sector_pivot.loc[dt]
        valid_sectors = sector_rets.dropna()
        agreeing = sum(1 for s in SECTOR_INDICES if s in valid_sectors.index and np.sign(valid_sectors[s]) == nifty_dir)

        participation = agreeing / 12.0

        if participation >= 0.75:
            score = 1
        elif participation <= 0.42:
            score = -1
        else:
            score = 0

        d6_raw_dict[dt] = participation
        d6_score_dict[dt] = score
        d6_agreeing_dict[dt] = agreeing

    d6_raw = pd.Series(d6_raw_dict, name="d6_raw")
    d6_score = pd.Series(d6_score_dict, name="d6_score")
    d6_agreeing = pd.Series(d6_agreeing_dict, name="d6_sectors_agreeing")

    # Combine all dimensions
    result = pd.DataFrame({
        "d3_score": d3_score,
        "d3_raw": d3_raw,
        "d4_score": d4_score,
        "d4_raw": d4_raw,
        "d5_score": d5_score,
        "d5_raw": d5_raw,
        "d6_score": d6_score,
        "d6_raw": d6_raw,
        "d6_sectors_agreeing": d6_agreeing,
    })

    return result


# ---------------------------------------------------------------------------
# DB schema migration
# ---------------------------------------------------------------------------

E4_COLUMNS = [
    ("label_e4_strict", "TEXT"),
    ("label_e4_moderate", "TEXT"),
    ("label_e4_loose", "TEXT"),
    ("d3_volume_score", "SMALLINT"),
    ("d4_dispersion_score", "SMALLINT"),
    ("d5_concentration_score", "SMALLINT"),
    ("d6_sector_score", "SMALLINT"),
    ("d3_raw", "REAL"),
    ("d4_raw", "REAL"),
    ("d5_raw", "REAL"),
    ("d6_raw", "REAL"),
]


def ensure_columns(conn):
    """Add E4 columns to regime_ground_truth if they don't exist."""
    cur = conn.cursor()
    for col, coltype in E4_COLUMNS:
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    conn = psycopg2.connect(DB_DSN)

    # ── Step 1: Load data ─────────────────────────────────────────────────
    nifty, cm, indices, vix, breadth = load_all_data(conn)

    # ── Step 2: Compute E3 labels ─────────────────────────────────────────
    print("\nComputing E3 labels...")
    e3_df = compute_e3_labels(nifty, vix, breadth)
    valid_e3 = e3_df.dropna(subset=["label_e3"]).copy()
    print(f"  E3 labels: {len(valid_e3)} days")
    print(f"  E3 distribution:\n{valid_e3['label_e3'].value_counts().to_string()}")

    trading_dates = valid_e3["date"].tolist()

    # ── Step 3: Compute D3–D6 dimensions ──────────────────────────────────
    dims = compute_all_dimensions(trading_dates, cm, indices, valid_e3[["date", "return_pct"]])
    print(f"\n  Dimensions computed for {len(dims)} dates")

    # ── Step 4: Merge E3 + dimensions + compute E4 labels ─────────────────
    print("\nComputing E4 labels (3 thresholds)...")
    valid_e3 = valid_e3.set_index("date")
    merged = valid_e3[["label_e3", "return_pct", "cir", "breadth_ratio"]].join(dims, how="left")

    # Fill NaN scores with 0
    for col in ["d3_score", "d4_score", "d5_score", "d6_score"]:
        merged[col] = merged[col].fillna(0).astype(int)
    merged["d6_sectors_agreeing"] = merged["d6_sectors_agreeing"].fillna(0).astype(int)

    # Compute E4 labels for each threshold
    for variant, threshold in [("strict", 3), ("moderate", 2), ("loose", 0)]:
        col_name = f"label_e4_{variant}"
        merged[col_name] = merged.apply(
            lambda row: compute_e4_label(
                row["label_e3"], int(row["d3_score"]), int(row["d4_score"]),
                int(row["d5_score"]), int(row["d6_score"]), threshold
            ), axis=1
        )

    merged = merged.reset_index()
    print(f"  Total rows with E4 labels: {len(merged)}")

    # ── Step 5: Ensure DB columns exist ───────────────────────────────────
    print("\nEnsuring E4 columns in regime_ground_truth...")
    ensure_columns(conn)

    # ── Step 6: Update DB ─────────────────────────────────────────────────
    print("\nUpdating regime_ground_truth with E4 data...")
    cur = conn.cursor()
    update_count = 0
    for i, row in merged.iterrows():
        cur.execute(
            """UPDATE regime_ground_truth SET
                label_e4_strict = %s, label_e4_moderate = %s, label_e4_loose = %s,
                d3_volume_score = %s, d4_dispersion_score = %s,
                d5_concentration_score = %s, d6_sector_score = %s,
                d3_raw = %s, d4_raw = %s, d5_raw = %s, d6_raw = %s
               WHERE date = %s""",
            (
                row["label_e4_strict"], row["label_e4_moderate"], row["label_e4_loose"],
                int(row["d3_score"]), int(row["d4_score"]),
                int(row["d5_score"]), int(row["d6_score"]),
                float(row["d3_raw"]) if not pd.isna(row["d3_raw"]) else None,
                float(row["d4_raw"]) if not pd.isna(row["d4_raw"]) else None,
                float(row["d5_raw"]) if not pd.isna(row["d5_raw"]) else None,
                float(row["d6_raw"]) if not pd.isna(row["d6_raw"]) else None,
                row["date"],
            ),
        )
        update_count += cur.rowcount
        if (i + 1) % 100 == 0:
            print(f"  Progress: {i + 1}/{len(merged)} days updated")

    conn.commit()
    print(f"  Updated {update_count} rows in regime_ground_truth")

    # ── Step 7: Export CSV ────────────────────────────────────────────────
    os.makedirs(DATA_DIR, exist_ok=True)
    csv_path = os.path.join(DATA_DIR, "e4_labels.csv")
    export_cols = [
        "date", "return_pct", "label_e3",
        "d3_score", "d3_raw", "d4_score", "d4_raw",
        "d5_score", "d5_raw", "d6_score", "d6_raw", "d6_sectors_agreeing",
        "label_e4_strict", "label_e4_moderate", "label_e4_loose",
    ]
    merged[export_cols].to_csv(csv_path, index=False)
    print(f"\nExported to {csv_path}")

    # ── Step 8: Summary stats ─────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("E4 BACKFILL SUMMARY")
    print("=" * 70)

    print(f"\nTotal trading days: {len(merged)}")

    for variant in ["e3", "e4_strict", "e4_moderate", "e4_loose"]:
        col = f"label_{variant}" if variant != "e3" else "label_e3"
        dist = merged[col].value_counts()
        total = len(merged)
        print(f"\n{variant.upper()} distribution:")
        for label in ["Trend-Up", "Range", "Trend-Down"]:
            count = dist.get(label, 0)
            pct = count / total * 100
            print(f"  {label:>12s}: {count:5d}  ({pct:5.1f}%)")

    # Flipped days
    print("\nFlipped days (E3 trend → E4 Range):")
    for variant in ["strict", "moderate", "loose"]:
        col = f"label_e4_{variant}"
        flipped = merged[(merged["label_e3"] != "Range") & (merged[col] == "Range")]
        print(f"  E4-{variant}: {len(flipped)} days demoted to Range")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
