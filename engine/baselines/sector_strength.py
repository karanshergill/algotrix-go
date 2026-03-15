"""
sector_strength.py — v2: Market-Cap Weighted Returns + Volume Metrics

Computes daily strength scores for 4 levels of sector/industry hierarchy:
  macro        → 12 groups  (sector_macro)
  sector       → 22 groups  (sector)
  industry     → 58 groups  (industry)
  sub_industry → 182 groups (industry_basic)

v2 improvements over v1:
  - Market-cap weighted returns (free-float market cap, fallback to market cap)
  - Volume metrics: vol_total_1d, vol_avg_20d, vol_ratio (RVOL)
  - Improved score weights with volume confirmation

Run: python3 engine/baselines/sector_strength.py
"""

import sys
from datetime import date
import psycopg2
import psycopg2.extras
import polars as pl
import numpy as np

DB_DSN = "postgresql://me:algotrix@localhost:5432/atdb"

RETURN_WINDOWS = {
    "ret_1d": 1,
    "ret_1w": 5,
    "ret_1m": 21,
    "ret_3m": 63,
    "ret_6m": 126,
    "ret_1y": 252,
}

# Score weights — v2: no 1D (too noisy), added RVOL
SCORE_WEIGHTS = {
    "ret_1w": 0.15,
    "ret_1m": 0.30,
    "ret_3m": 0.25,
    "ret_6m": 0.20,
    "vol_ratio": 0.10,
}

LEVELS = [
    ("macro",        "sector_macro"),
    ("sector",       "sector"),
    ("industry",     "industry"),
    ("sub_industry", "industry_basic"),
]


def fetch_symbols(conn) -> pl.DataFrame:
    """Load active symbols with sector hierarchy + market cap for weighting."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT isin, sector_macro, sector, industry, industry_basic,
                   COALESCE(free_float_market_cap, market_cap) AS mcap
            FROM symbols
            WHERE status = 'active'
              AND sector_macro IS NOT NULL
              AND sector IS NOT NULL
              AND industry IS NOT NULL
              AND industry_basic IS NOT NULL
        """)
        rows = cur.fetchall()
    return pl.DataFrame(
        rows,
        schema={
            "isin": pl.Utf8,
            "sector_macro": pl.Utf8,
            "sector": pl.Utf8,
            "industry": pl.Utf8,
            "industry_basic": pl.Utf8,
            "mcap": pl.Float64,
        },
        orient="row",
    )


def fetch_ohlcv(conn, isins: list[str]) -> pl.DataFrame:
    """Load last 260 trading days of OHLCV (close + volume) for the given ISINs."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT isin, timestamp::date AS date, close, volume
            FROM nse_cm_ohlcv_1d
            WHERE isin = ANY(%s)
            ORDER BY isin, date DESC
            LIMIT 260 * %s
        """, (isins, len(isins)))
        rows = cur.fetchall()
    if not rows:
        return pl.DataFrame(schema={
            "isin": pl.Utf8, "date": pl.Date,
            "close": pl.Float64, "volume": pl.Int64,
        })
    return pl.DataFrame(
        rows,
        schema=["isin", "date", "close", "volume"],
        orient="row",
    )


def compute_stock_metrics(ohlcv: pl.DataFrame) -> pl.DataFrame:
    """
    For each ISIN, compute:
      - returns at each window (1D, 1W, 1M, 3M, 6M, 1Y)
      - volume today
      - average volume over last 20 trading days
      - volume ratio (RVOL = vol_today / avg_vol_20d)
      - advance/decline direction
    """
    ohlcv = ohlcv.sort(["isin", "date"], descending=[False, True])

    # Row number within each isin (0 = most recent)
    ohlcv = ohlcv.with_columns(
        pl.int_range(pl.len(), dtype=pl.UInt32).over("isin").alias("row_num")
    )

    # --- Returns ---
    today_df = ohlcv.filter(pl.col("row_num") == 0).select([
        "isin",
        pl.col("close").alias("c0"),
        pl.col("volume").alias("vol_today"),
    ])

    result = today_df
    for col_name, offset in RETURN_WINDOWS.items():
        lag_df = (
            ohlcv.filter(pl.col("row_num") == offset)
            .select(["isin", pl.col("close").alias(f"c{offset}")])
        )
        result = result.join(lag_df, on="isin", how="left")
        offset_col = f"c{offset}"
        result = result.with_columns(
            pl.when(
                pl.col(offset_col).is_not_null() & (pl.col(offset_col) != 0)
            )
            .then((pl.col("c0") - pl.col(offset_col)) / pl.col(offset_col) * 100)
            .otherwise(None)
            .alias(col_name)
        ).drop(offset_col)

    # --- Volume: 20-day average ---
    vol_20d = (
        ohlcv.filter(pl.col("row_num") < 20)
        .group_by("isin")
        .agg(pl.col("volume").mean().alias("vol_avg_20d"))
    )
    result = result.join(vol_20d, on="isin", how="left")

    # RVOL per stock
    result = result.with_columns(
        pl.when(
            pl.col("vol_avg_20d").is_not_null() & (pl.col("vol_avg_20d") > 0)
        )
        .then(pl.col("vol_today").cast(pl.Float64) / pl.col("vol_avg_20d"))
        .otherwise(None)
        .alias("stock_rvol")
    )

    # Advance / decline
    result = result.with_columns(
        pl.when(pl.col("ret_1d") > 0).then(pl.lit("adv"))
        .when(pl.col("ret_1d") < 0).then(pl.lit("dec"))
        .otherwise(pl.lit("unch"))
        .alias("direction")
    )

    return result.drop("c0")


def percentile_rank(series: pl.Series) -> pl.Series:
    """Convert a numeric series to percentile rank (0-100)."""
    arr = series.to_numpy().astype(float)
    valid_mask = ~np.isnan(arr)
    ranks = np.full_like(arr, np.nan, dtype=float)
    if valid_mask.sum() > 1:
        valid_vals = arr[valid_mask]
        n = len(valid_vals)
        sorted_vals = np.sort(valid_vals)
        for i, v in enumerate(arr):
            if not np.isnan(v):
                ranks[i] = (np.searchsorted(sorted_vals, v, side="right") / n) * 100
    elif valid_mask.sum() == 1:
        ranks[valid_mask] = 50.0
    return pl.Series(series.name, ranks)


def aggregate_level(
    metrics_df: pl.DataFrame,
    symbols_df: pl.DataFrame,
    level_col: str,
    level_name: str,
) -> pl.DataFrame:
    """
    Aggregate per-stock metrics to group level using market-cap weighting.
    Stocks without market cap fall back to equal weight within their group.
    """
    # Join metrics with sector hierarchy + mcap
    merged = metrics_df.join(
        symbols_df.select(["isin", level_col, "mcap"]),
        on="isin",
        how="left",
    ).filter(pl.col(level_col).is_not_null())

    # Fill null mcap with group median (so they get ~equal weight within group)
    merged = merged.with_columns(
        pl.col("mcap")
        .fill_null(pl.col("mcap").median().over(level_col))
        .fill_null(1.0)  # fallback if entire group has no mcap
        .alias("weight")
    )

    return_cols = list(RETURN_WINDOWS.keys())

    # --- Market-cap weighted returns ---
    # For each return column: weighted_avg = sum(ret * weight) / sum(weight)
    weighted_ret_exprs = []
    for col in return_cols:
        # Numerator: sum of (return * weight), skipping nulls
        num = (pl.col(col) * pl.col("weight")).sum().alias(f"_wsum_{col}")
        den = (
            pl.when(pl.col(col).is_not_null())
            .then(pl.col("weight"))
            .otherwise(0)
            .sum()
            .alias(f"_wden_{col}")
        )
        weighted_ret_exprs.extend([num, den])

    agg_exprs = [
        pl.len().alias("stock_count"),
        # Volume: sum across group
        pl.col("vol_today").sum().alias("vol_total_1d"),
        pl.col("vol_avg_20d").sum().alias("vol_avg_20d_sum"),
        # A/D counts
        (pl.col("direction") == "adv").sum().alias("adv_count"),
        (pl.col("direction") == "dec").sum().alias("dec_count"),
        (pl.col("direction") == "unch").sum().alias("unch_count"),
        *weighted_ret_exprs,
    ]

    grouped = (
        merged
        .group_by(level_col)
        .agg(agg_exprs)
        .rename({level_col: "group_name"})
    )

    # Compute weighted averages from sums
    for col in return_cols:
        grouped = grouped.with_columns(
            pl.when(pl.col(f"_wden_{col}") > 0)
            .then(pl.col(f"_wsum_{col}") / pl.col(f"_wden_{col}"))
            .otherwise(None)
            .alias(col)
        ).drop(f"_wsum_{col}", f"_wden_{col}")

    # Group RVOL = vol_total_1d / vol_avg_20d_sum
    grouped = grouped.with_columns(
        pl.when(
            pl.col("vol_avg_20d_sum").is_not_null() & (pl.col("vol_avg_20d_sum") > 0)
        )
        .then(pl.col("vol_total_1d").cast(pl.Float64) / pl.col("vol_avg_20d_sum"))
        .otherwise(None)
        .alias("vol_ratio")
    ).rename({"vol_avg_20d_sum": "vol_avg_20d"})

    # --- Composite score ---
    # Normalize vol_ratio to a -100..+100 scale: (rvol - 1) * 100, capped
    grouped = grouped.with_columns(
        ((pl.col("vol_ratio").fill_null(1.0) - 1.0) * 100)
        .clip(-100, 100)
        .alias("vol_ratio_scaled")
    )

    weight_expr = sum(
        pl.col(k if k != "vol_ratio" else "vol_ratio_scaled").fill_null(0) * v
        for k, v in SCORE_WEIGHTS.items()
    )
    grouped = grouped.with_columns(weight_expr.alias("weighted_composite"))

    # Percentile rank within this level
    score_series = percentile_rank(grouped["weighted_composite"])
    grouped = grouped.with_columns(
        score_series.alias("score"),
        pl.lit(level_name).alias("level"),
    ).drop("weighted_composite", "vol_ratio_scaled")

    return grouped


def upsert_rows(conn, rows: list[dict]) -> None:
    """Upsert sector_strength rows with volume columns."""
    if not rows:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO sector_strength (
                date, level, group_name, stock_count, score,
                ret_1d, ret_1w, ret_1m, ret_3m, ret_6m, ret_1y,
                adv_count, dec_count, unch_count,
                vol_total_1d, vol_avg_20d, vol_ratio
            ) VALUES %s
            ON CONFLICT (date, level, group_name) DO UPDATE SET
                stock_count  = EXCLUDED.stock_count,
                score        = EXCLUDED.score,
                ret_1d       = EXCLUDED.ret_1d,
                ret_1w       = EXCLUDED.ret_1w,
                ret_1m       = EXCLUDED.ret_1m,
                ret_3m       = EXCLUDED.ret_3m,
                ret_6m       = EXCLUDED.ret_6m,
                ret_1y       = EXCLUDED.ret_1y,
                adv_count    = EXCLUDED.adv_count,
                dec_count    = EXCLUDED.dec_count,
                unch_count   = EXCLUDED.unch_count,
                vol_total_1d = EXCLUDED.vol_total_1d,
                vol_avg_20d  = EXCLUDED.vol_avg_20d,
                vol_ratio    = EXCLUDED.vol_ratio
            """,
            [
                (
                    r["date"], r["level"], r["group_name"], r["stock_count"],
                    r["score"],
                    r.get("ret_1d"), r.get("ret_1w"), r.get("ret_1m"),
                    r.get("ret_3m"), r.get("ret_6m"), r.get("ret_1y"),
                    r["adv_count"], r["dec_count"], r["unch_count"],
                    r.get("vol_total_1d"), r.get("vol_avg_20d"),
                    r.get("vol_ratio"),
                )
                for r in rows
            ],
        )
    conn.commit()


def null_safe(val):
    """Convert NaN/inf to None for DB insert."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f or f == float("inf") or f == float("-inf")) else round(f, 4)
    except (TypeError, ValueError):
        return None


def int_safe(val):
    """Convert to int or None."""
    if val is None:
        return None
    try:
        i = int(val)
        return i if i == i else None  # NaN check
    except (TypeError, ValueError):
        return None


def main():
    target_date = date.today()
    print(f"[sector_strength v2] Computing for {target_date}")

    conn = psycopg2.connect(DB_DSN)
    try:
        # 1. Load symbols with mcap
        symbols_df = fetch_symbols(conn)
        isins = symbols_df["isin"].to_list()
        mcap_count = symbols_df.filter(pl.col("mcap").is_not_null()).height
        print(f"[sector_strength v2] {len(isins)} active symbols ({mcap_count} with market cap)")

        if not isins:
            print("[sector_strength v2] No symbols found, exiting")
            return

        # 2. Load OHLCV (close + volume)
        ohlcv_df = fetch_ohlcv(conn, isins)
        print(f"[sector_strength v2] {len(ohlcv_df)} OHLCV rows loaded")

        if ohlcv_df.is_empty():
            print("[sector_strength v2] No OHLCV data found, exiting")
            return

        # 3. Compute per-stock metrics (returns + volume)
        metrics_df = compute_stock_metrics(ohlcv_df)
        print(f"[sector_strength v2] Metrics computed for {len(metrics_df)} stocks")

        # 4. Aggregate for each level (mcap-weighted)
        all_rows: list[dict] = []
        for level_name, level_col in LEVELS:
            level_df = aggregate_level(metrics_df, symbols_df, level_col, level_name)
            print(f"[sector_strength v2] {level_name}: {len(level_df)} groups")

            for row in level_df.to_dicts():
                all_rows.append({
                    "date":        target_date,
                    "level":       level_name,
                    "group_name":  row["group_name"],
                    "stock_count": int(row["stock_count"]),
                    "score":       null_safe(row.get("score")),
                    "ret_1d":      null_safe(row.get("ret_1d")),
                    "ret_1w":      null_safe(row.get("ret_1w")),
                    "ret_1m":      null_safe(row.get("ret_1m")),
                    "ret_3m":      null_safe(row.get("ret_3m")),
                    "ret_6m":      null_safe(row.get("ret_6m")),
                    "ret_1y":      null_safe(row.get("ret_1y")),
                    "adv_count":   int(row["adv_count"]),
                    "dec_count":   int(row["dec_count"]),
                    "unch_count":  int(row["unch_count"]),
                    "vol_total_1d": int_safe(row.get("vol_total_1d")),
                    "vol_avg_20d":  int_safe(row.get("vol_avg_20d")),
                    "vol_ratio":    null_safe(row.get("vol_ratio")),
                })

        # 5. Upsert
        upsert_rows(conn, all_rows)
        print(f"[sector_strength v2] Upserted {len(all_rows)} rows for {target_date}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
