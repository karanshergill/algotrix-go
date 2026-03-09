"""Volume allocation across price buckets using range-overlap method."""

import math

import numpy as np

from db.read.tick import get_tick_size


def allocate_volume(highs, lows, closes, volumes, price_min, bucket_size, n_buckets, cfg):
    """Allocate bar volumes to price buckets proportional to range overlap.

    Returns:
        np.ndarray of shape (n_buckets,) with allocated volumes (float64).
    """
    bucket_volumes = np.zeros(n_buckets, dtype=np.float64)

    for i in range(len(highs)):
        tick_size = get_tick_size(cfg, closes[i])
        bar_range = max(highs[i] - lows[i], tick_size)

        low_idx = int(math.floor((lows[i] - price_min) / bucket_size))
        high_idx = int(math.ceil((highs[i] - price_min) / bucket_size))

        for b in range(max(0, low_idx), min(n_buckets, high_idx + 1)):
            bucket_bottom = price_min + b * bucket_size
            bucket_top = bucket_bottom + bucket_size
            overlap = min(bucket_top, highs[i]) - max(bucket_bottom, lows[i])
            if overlap > 0:
                bucket_volumes[b] += volumes[i] * (overlap / bar_range)

    return bucket_volumes
