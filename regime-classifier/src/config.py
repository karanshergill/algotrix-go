"""Configuration constants and DB connection settings."""

import os

# DB connection — matches Go pipeline: PGPASSWORD=algotrix psql -h localhost -U me -d atdb
DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": int(os.environ.get("PGPORT", 5432)),
    "dbname": os.environ.get("PGDATABASE", "atdb"),
    "user": os.environ.get("PGUSER", "me"),
    "password": os.environ.get("PGPASSWORD", "algotrix"),
}

# Versioning — bump when feature/classifier logic changes
FEATURE_VERSION = "v1.0.0"
CLASSIFIER_VERSION = "v1.0.0"

# Regime labels
REGIME_LABELS = [
    "strong_bull",
    "breakout_setup",
    "volatile_choppy",
    "bearish",
    "neutral",
]

# Index names in nse_indices_daily
NIFTY_50_INDEX = "Nifty 50"
INDIA_VIX_INDEX = "India VIX"

# F&O symbols
NIFTY_FO_SYMBOL = "NIFTY"

# Lookback windows
ATR_PERIOD = 14
ADX_PERIOD = 14
EMA_PERIOD = 20
BB_PERIOD = 20
BB_STDDEV = 2.0
PERCENTILE_WINDOW = 60
AD_RATIO_AVG_WINDOW = 5
HURST_WINDOW = 100  # nolds recommends >= 100

# Smoother
EMA_SMOOTH_SPAN = 3
HYSTERESIS_DAYS = 2
SHOCK_SIGMA_THRESHOLD = 2.0
SHOCK_LOOKBACK = 20

# Minimum data requirements — need enough history for longest lookback
MIN_HISTORY_DAYS = HURST_WINDOW + 10  # ~110 days
