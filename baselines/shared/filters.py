"""Outlier filtering utilities for baseline plugins."""

import numpy as np


def filter_outliers_mad(highs, lows, closes, volumes, k=10):
    """Filter bars with prices outside k * MAD from median close.

    All inputs and outputs are numpy arrays.
    """
    median_price = np.median(closes)
    mad = np.median(np.abs(closes - median_price))

    if mad == 0:
        return highs, lows, closes, volumes

    mask = (highs <= median_price + k * mad) & (lows >= median_price - k * mad)
    return highs[mask], lows[mask], closes[mask], volumes[mask]
