"""Point of Control (POC) detection."""

import numpy as np


def find_poc(bucket_volumes, bucket_prices):
    """Find the bucket with highest volume.

    Returns:
        (poc_idx, poc_price)
    """
    poc_idx = int(np.argmax(bucket_volumes))
    poc_price = float(bucket_prices[poc_idx])
    return poc_idx, poc_price
