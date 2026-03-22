"""Regime profile vectors — hand-tuned centroids for Euclidean classification.

Each profile is a 4D vector: [Volatility, Trend, Participation, Sentiment]
All values on 0-100 scale (percentile-normalized).

These are the "ideal" regimes. The classifier measures how close today's
market is to each profile and picks the nearest one.
"""

import numpy as np

# Profile centroids: {label: [vol, trend, participation, sentiment]}
# Volatility: higher = more volatile (VIX high, ATR high, BBW wide)
# Trend: higher = stronger trend (ADX high, EMA distance large)
# Participation: higher = broader participation (A/D ratio high, breadth wide)
# Sentiment: higher = more bullish (PCR low-ish, futures premium)

REGIME_PROFILES = {
    "strong_bull":     np.array([25.0, 80.0, 80.0, 70.0]),
    "breakout_setup":  np.array([35.0, 50.0, 60.0, 55.0]),
    "volatile_choppy": np.array([80.0, 25.0, 35.0, 40.0]),
    "bearish":         np.array([60.0, 70.0, 30.0, 25.0]),
    "neutral":         np.array([45.0, 45.0, 50.0, 50.0]),
}
