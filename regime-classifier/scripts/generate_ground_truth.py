#!/usr/bin/env python3
"""Generate ground truth labels for all historical trading dates.

Computes both coincident and predictive labels for regime validation.
Writes to regime_ground_truth table.

Usage:
    PGPASSWORD=algotrix python scripts/generate_ground_truth.py
"""

import logging
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from src.db import _read_sql, get_connection, transaction, upsert_ground_truth
from src.ground_truth import compute_coincident_truth, compute_predictive_truth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def generate_all():
    """Generate ground truth for all available dates."""
    # Fetch Nifty close + VIX for all dates
    nifty = _read_sql("""
        SELECT date, close FROM nse_indices_daily
        WHERE index = 'Nifty 50'
        ORDER BY date ASC
    """)
    vix = _read_sql("""
        SELECT date, close as vix_close FROM nse_indices_daily
        WHERE index = 'India VIX'
        ORDER BY date ASC
    """)

    # Fetch A/D data from CM bhavcopy — one query for all dates
    logger.info("Fetching breadth data (this may take a moment)...")
    breadth = _read_sql("""
        SELECT date,
               SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as advances,
               SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) as declines
        FROM nse_cm_bhavcopy
        GROUP BY date
        ORDER BY date ASC
    """)

    # Merge
    nifty["prev_close"] = nifty["close"].shift(1)
    nifty["nifty_return"] = nifty["close"] / nifty["prev_close"] - 1
    nifty["next_day_return"] = nifty["nifty_return"].shift(-1)

    vix["prev_vix"] = vix["vix_close"].shift(1)
    vix["vix_change_pct"] = (vix["vix_close"] - vix["prev_vix"]) / vix["prev_vix"] * 100

    # Merge all on date
    merged = nifty.merge(vix[["date", "vix_change_pct"]], on="date", how="left")
    merged = merged.merge(breadth, on="date", how="left")
    merged["breadth_ratio"] = merged["advances"] / (merged["advances"] + merged["declines"])

    # Drop first row (no prev_close) and NaN rows
    merged = merged.dropna(subset=["nifty_return", "breadth_ratio", "vix_change_pct"])

    logger.info("Computing ground truth for %d dates...", len(merged))
    count = 0

    with transaction() as conn:
        for _, row in merged.iterrows():
            coincident = compute_coincident_truth(
                row["nifty_return"],
                row["breadth_ratio"],
                row["vix_change_pct"],
            )

            next_day_ret = row.get("next_day_return")
            predictive = None
            if next_day_ret is not None and not np.isnan(next_day_ret):
                predictive = compute_predictive_truth(next_day_ret)
            else:
                next_day_ret = None

            upsert_ground_truth(conn, {
                "date": row["date"],
                "nifty_return": float(row["nifty_return"]),
                "breadth_ratio": float(row["breadth_ratio"]),
                "vix_change_pct": float(row["vix_change_pct"]),
                "coincident_label": coincident,
                "next_day_return": float(next_day_ret) if next_day_ret is not None else None,
                "predictive_label": predictive,
            })
            count += 1

    logger.info("Done: %d ground truth rows written", count)

    # Print distribution
    gt = _read_sql("SELECT coincident_label, COUNT(*) as n FROM regime_ground_truth GROUP BY coincident_label")
    logger.info("Coincident distribution:\n%s", gt.to_string(index=False))
    pt = _read_sql("SELECT predictive_label, COUNT(*) as n FROM regime_ground_truth WHERE predictive_label IS NOT NULL GROUP BY predictive_label")
    logger.info("Predictive distribution:\n%s", pt.to_string(index=False))


if __name__ == "__main__":
    generate_all()
