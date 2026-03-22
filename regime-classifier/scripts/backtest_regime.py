#!/usr/bin/env python3
"""Run full regime scoring pipeline on all historical dates.

For each date D:
1. Compute features (anti-leakage: data <= D)
2. Score all 5 dimensions
3. Compute composite score + regime label
4. Predict next-day regime from leading indicators
5. Store results in regime_backtest table

Usage:
    PGPASSWORD=algotrix python scripts/backtest_regime.py
    PGPASSWORD=algotrix python scripts/backtest_regime.py --bounds-mode walkforward
    PGPASSWORD=algotrix python scripts/backtest_regime.py --from 2024-01-01 --to 2026-03-19
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import (
    _read_sql,
    fetch_all_trading_dates,
    transaction,
    upsert_backtest,
)
from src.features import DataNotAvailableError, compute_features
from src.predictor import predict_next_day
from src.scorer import score_date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "features_cache.json")


def load_ground_truth() -> dict:
    """Load ground truth labels keyed by date string."""
    gt = _read_sql("SELECT date, coincident_label, predictive_label FROM regime_ground_truth ORDER BY date")
    result = {}
    for _, row in gt.iterrows():
        result[str(row["date"])] = {
            "coincident": row["coincident_label"],
            "predictive": row.get("predictive_label"),
        }
    return result


def load_feature_cache() -> dict:
    """Load precomputed feature cache if available."""
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


def run_backtest(from_date=None, to_date=None, bounds_mode="production"):
    dates = fetch_all_trading_dates()
    if from_date:
        dates = [d for d in dates if d >= from_date]
    if to_date:
        dates = [d for d in dates if d <= to_date]

    logger.info("Backtest: %d dates (%s to %s), bounds=%s",
                len(dates), dates[0], dates[-1], bounds_mode)

    ground_truth = load_ground_truth()
    feature_cache = load_feature_cache()

    scored = 0
    skipped = 0
    batch = []
    batch_size = 50

    for i, d in enumerate(dates):
        date_key = str(d)

        # Compute features (use cache for raw indicators, but recompute full features for meta)
        try:
            features = compute_features(d)
        except DataNotAvailableError:
            skipped += 1
            continue
        except Exception as e:
            logger.warning("Error on %s: %s", d, e)
            skipped += 1
            continue

        # Score
        result = score_date(features, bounds_mode=bounds_mode, target_date=d)
        prediction = predict_next_day(features, bounds_mode=bounds_mode, target_date=d)

        # Ground truth lookup
        gt = ground_truth.get(date_key, {})

        row = {
            "date": d,
            "vol_score": result["vol_score"],
            "trend_score": result["trend_score"],
            "participation_score": result["participation_score"],
            "sentiment_score": result["sentiment_score"],
            "institutional_flow_score": result["institutional_flow_score"],
            "composite_score": result["composite_score"],
            "regime_label": result["regime_label"],
            "predicted_label": prediction["predicted_label"],
            "predicted_confidence": prediction["confidence"],
            "coincident_truth": gt.get("coincident"),
            "predictive_truth": gt.get("predictive"),
            "availability_regime": result["availability_regime"],
            "missing_indicators": result["missing_indicators"],
        }
        batch.append(row)
        scored += 1

        if len(batch) >= batch_size:
            _flush_batch(batch)
            batch = []

        if scored % 100 == 0:
            logger.info("Progress: %d scored, %d skipped (%d/%d)", scored, skipped, i + 1, len(dates))

    if batch:
        _flush_batch(batch)

    logger.info("Backtest complete: %d scored, %d skipped", scored, skipped)


def _flush_batch(batch):
    with transaction() as conn:
        for row in batch:
            upsert_backtest(conn, row)


def main():
    parser = argparse.ArgumentParser(description="Run regime backtest")
    parser.add_argument("--from", dest="from_date", type=str, default=None)
    parser.add_argument("--to", dest="to_date", type=str, default=None)
    parser.add_argument("--bounds-mode", choices=["production", "walkforward"], default="production")
    args = parser.parse_args()

    from_d = datetime.strptime(args.from_date, "%Y-%m-%d").date() if args.from_date else None
    to_d = datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else None

    run_backtest(from_date=from_d, to_date=to_d, bounds_mode=args.bounds_mode)


if __name__ == "__main__":
    main()
