"""Price bucket construction for volume profile."""

import math

import numpy as np


def build_buckets(lows, highs, bucket_size):
    """Build price buckets spanning the low-high range.

    Returns:
        (price_min, price_max, n_buckets, bucket_prices)
    """
    price_min = math.floor(min(lows) / bucket_size) * bucket_size
    price_max = math.ceil(max(highs) / bucket_size) * bucket_size
    if price_min == price_max:
        price_max += bucket_size
    n_buckets = max(1, int(math.ceil((price_max - price_min) / bucket_size)))
    bucket_prices = np.array([price_min + (i + 0.5) * bucket_size for i in range(n_buckets)])
    return price_min, price_max, n_buckets, bucket_prices
