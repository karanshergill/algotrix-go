#!/usr/bin/env python3
"""Calibrate indicator bounds from historical data.

Two modes:
1. Production: p1/p99 across all dates → data/indicator_bounds.json
2. Walk-forward: expanding-window p1/p99 per date → data/walkforward_bounds.json

Usage:
    PGPASSWORD=algotrix python scripts/calibrate_bounds.py --mode production
    PGPASSWORD=algotrix python scripts/calibrate_bounds.py --mode walkforward
    PGPASSWORD=algotrix python scripts/calibrate_bounds.py --mode both
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from src.db import fetch_all_trading_dates
from src.features import DataNotAvailableError, compute_features
from src.scorer import DEFAULT_INDICATOR_BOUNDS, DIMENSION_WEIGHTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE_PATH = os.path.join(DATA_DIR, "features_cache.json")
BOUNDS_PATH = os.path.join(DATA_DIR, "indicator_bounds.json")
WALKFORWARD_PATH = os.path.join(DATA_DIR, "walkforward_bounds.json")

MIN_WALKFORWARD_WINDOW = 250  # ~1 year


def get_all_indicator_names() -> list[str]:
    """Get list of all indicator names from dimension weights."""
    names = []
    for dim_weights in DIMENSION_WEIGHTS.values():
        names.extend(dim_weights.keys())
    return names


def load_or_compute_features(dates: list, force_recompute: bool = False) -> dict:
    """Compute features for all dates, with caching."""
    cache = {}
    if not force_recompute and os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            cache = json.load(f)
        logger.info("Loaded %d cached feature rows", len(cache))

    indicator_names = get_all_indicator_names()
    computed = 0
    skipped = 0

    for i, d in enumerate(dates):
        date_key = str(d)
        if date_key in cache:
            continue

        try:
            features = compute_features(d)
            cache[date_key] = {k: features.get(k) for k in indicator_names}
            computed += 1
        except DataNotAvailableError:
            skipped += 1
        except Exception as e:
            logger.warning("Error computing %s: %s", d, e)
            skipped += 1

        if (computed + skipped) % 100 == 0:
            logger.info("Progress: %d/%d dates (computed=%d, skipped=%d)", i + 1, len(dates), computed, skipped)
            # Save checkpoint
            with open(CACHE_PATH, "w") as f:
                json.dump(cache, f)

    # Final save
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)
    logger.info("Total: %d computed, %d skipped, %d cached", computed, skipped, len(cache))

    return cache


def compute_production_bounds(cache: dict) -> dict:
    """Compute p1/p99 bounds across all dates."""
    indicator_names = get_all_indicator_names()
    bounds = {}

    for name in indicator_names:
        values = [cache[d].get(name) for d in cache if cache[d].get(name) is not None]
        if len(values) < 10:
            logger.warning("Too few values for %s (%d), using defaults", name, len(values))
            default = DEFAULT_INDICATOR_BOUNDS.get(name, (0, 100, False))
            bounds[name] = [default[0], default[1]]
            continue

        p1 = float(np.percentile(values, 1))
        p99 = float(np.percentile(values, 99))

        # Ensure min != max
        if p1 == p99:
            p1 = p1 - 1
            p99 = p99 + 1

        bounds[name] = [p1, p99]

    return bounds


def compute_walkforward_bounds(cache: dict) -> dict:
    """Compute expanding-window p1/p99 for each date."""
    indicator_names = get_all_indicator_names()
    sorted_dates = sorted(cache.keys())
    wf_bounds = {}

    for i, d in enumerate(sorted_dates):
        if i < MIN_WALKFORWARD_WINDOW:
            # Use frozen initial bounds for dates before min window
            continue

        window_dates = sorted_dates[:i + 1]
        date_bounds = {}

        for name in indicator_names:
            values = [cache[wd].get(name) for wd in window_dates if cache[wd].get(name) is not None]
            if len(values) < 10:
                continue
            p1 = float(np.percentile(values, 1))
            p99 = float(np.percentile(values, 99))
            if p1 == p99:
                p1 -= 1
                p99 += 1
            date_bounds[name] = [p1, p99]

        wf_bounds[d] = date_bounds

    return wf_bounds


def main():
    parser = argparse.ArgumentParser(description="Calibrate indicator bounds")
    parser.add_argument("--mode", choices=["production", "walkforward", "both"], default="production")
    parser.add_argument("--force-recompute", action="store_true", help="Ignore feature cache")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    dates = fetch_all_trading_dates()
    logger.info("Found %d trading dates", len(dates))

    cache = load_or_compute_features(dates, force_recompute=args.force_recompute)

    if args.mode in ("production", "both"):
        logger.info("Computing production bounds...")
        bounds = compute_production_bounds(cache)
        with open(BOUNDS_PATH, "w") as f:
            json.dump(bounds, f, indent=2)
        logger.info("Production bounds written to %s (%d indicators)", BOUNDS_PATH, len(bounds))

        # Print summary
        for name, (lo, hi) in sorted(bounds.items()):
            logger.info("  %-30s  [%10.3f, %10.3f]", name, lo, hi)

    if args.mode in ("walkforward", "both"):
        logger.info("Computing walk-forward bounds...")
        wf = compute_walkforward_bounds(cache)
        with open(WALKFORWARD_PATH, "w") as f:
            json.dump(wf, f)
        logger.info("Walk-forward bounds written to %s (%d dates)", WALKFORWARD_PATH, len(wf))


if __name__ == "__main__":
    main()
