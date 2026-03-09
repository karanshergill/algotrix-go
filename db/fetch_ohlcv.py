"""Fetch OHLCV data from QuestDB, optimized with Pandas."""

import logging
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from db.conns.py_conn import get_questdb_engine
from utils import chunked, isin_where_clause

logger = logging.getLogger(__name__)


def fetch_ohlcv(
    cfg: Dict[str, Any],
    source_key: str,
    isins: List[str],
    start_date: str,
    end_date: str,
    chunk_size: int = 50,
) -> Dict[Tuple[str, str], Tuple[np.ndarray, ...]]:
    """Fetch OHLCV data grouped by (isin, trade_date) using Pandas.

    Args:
        cfg: Baseline config dict (needs cfg["sources"][source_key]).
        source_key: Key into cfg["sources"] (e.g. "ohlcv_5s").
        isins: List of ISIN strings to fetch.
        start_date: Start date string (YYYY-MM-DD).
        end_date: End date string (YYYY-MM-DD).
        chunk_size: Number of ISINs per query batch.

    Returns:
        Dict mapping (isin, trade_date) to
        (highs, lows, closes, volumes) as numpy arrays.
    """
    try:
        source_table = cfg["sources"][source_key]
    except KeyError:
        logger.error("Source key '%s' not found in configuration.", source_key)
        raise

    engine = get_questdb_engine()
    results = {}

    for chunk in chunked(isins, size=chunk_size):
        where = isin_where_clause(chunk, start_date, end_date)
        sql = (
            f"SELECT isin, "
            f"to_str(timestamp_floor('d', timestamp), 'yyyy-MM-dd') AS trade_date, "
            f"high, low, close, volume "
            f"FROM {source_table} "
            f"WHERE {where} "
            f"ORDER BY isin, trade_date, timestamp"
        )

        try:
            df = pd.read_sql_query(sql, con=engine)
        except Exception as e:
            logger.warning("Database query failed for chunk: %s", e)
            continue

        if df.empty:
            continue

        # Vectorized null removal
        df = df.dropna(subset=["high", "low", "close", "volume"])

        # Fast grouping via Pandas
        for (isin, td), group in df.groupby(["isin", "trade_date"]):
            results[(isin, td)] = (
                group["high"].to_numpy(dtype=np.float64),
                group["low"].to_numpy(dtype=np.float64),
                group["close"].to_numpy(dtype=np.float64),
                group["volume"].to_numpy(dtype=np.int64),
            )

    return results
