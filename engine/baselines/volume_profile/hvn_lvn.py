"""High Volume Node (HVN) and Low Volume Node (LVN) detection."""

import numpy as np


def detect_hvn_lvn(bucket_volumes, bucket_prices, hvn_percentile, lvn_percentile):
    """Detect HVN and LVN levels using percentile thresholds on active buckets.

    Returns:
        (hvn_levels, lvn_levels, hvn_count, lvn_count)
    """
    active_volumes = bucket_volumes[bucket_volumes > 0]
    if len(active_volumes) == 0:
        return [], [], 0, 0

    p_high = np.percentile(active_volumes, hvn_percentile)
    p_low = np.percentile(active_volumes, lvn_percentile)

    hvn_mask = bucket_volumes >= p_high
    lvn_mask = (bucket_volumes <= p_low) & (bucket_volumes > 0)

    hvn_levels = [round(float(p), 2) for p in bucket_prices[hvn_mask]]
    lvn_levels = [round(float(p), 2) for p in bucket_prices[lvn_mask]]

    return hvn_levels, lvn_levels, int(hvn_mask.sum()), int(lvn_mask.sum())
