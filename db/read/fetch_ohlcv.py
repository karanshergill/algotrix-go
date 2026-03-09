"""Fetch OHLCV data from QuestDB, grouped by (isin, trade_date)."""

import numpy as np

from baselines.baseline_plugin import questdb_query
from utils import chunked, isin_where_clause


def fetch_ohlcv(cfg, source_key, isins, start_date, end_date):
    """Fetch OHLCV data grouped by (isin, trade_date).

    Returns:
        dict mapping (isin, trade_date) to (highs, lows, closes, volumes) numpy arrays.
    """
    source_table = cfg["sources"][source_key]
    groups = {}

    for chunk in chunked(isins, size=10):
        where = isin_where_clause(chunk, start_date, end_date)
        sql = (
            f"SELECT isin, timestamp_floor('d', timestamp) AS trade_date, "
            f"high, low, close, volume "
            f"FROM {source_table} "
            f"WHERE {where} "
            f"ORDER BY isin, trade_date, timestamp"
        )
        rows = questdb_query(cfg, sql)
        if not rows:
            continue

        for r in rows:
            if any(r[c] is None for c in ("high", "low", "close", "volume")):
                continue
            isin = r["isin"]
            td = str(r["trade_date"])[:10]
            key = (isin, td)
            if key not in groups:
                groups[key] = ([], [], [], [])
            highs, lows, closes, volumes = groups[key]
            highs.append(float(r["high"]))
            lows.append(float(r["low"]))
            closes.append(float(r["close"]))
            volumes.append(int(r["volume"]))

    # Convert lists to numpy arrays
    result = {}
    for key, (highs, lows, closes, volumes) in groups.items():
        result[key] = (
            np.array(highs, dtype=np.float64),
            np.array(lows, dtype=np.float64),
            np.array(closes, dtype=np.float64),
            np.array(volumes, dtype=np.int64),
        )

    return result
