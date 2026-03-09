"""Fetch OHLCV data from QuestDB, optimized with Polars."""

import logging
from typing import Any, Dict, List, Tuple

import numpy as np
import polars as pl

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
    """Fetch OHLCV data grouped by (isin, trade_date) using Polars.

    Uses psycopg2 cursor to fetch rows (QuestDB doesn't support ConnectorX's
    BINARY protocol), then processes with Polars for fast grouping.

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
            conn = engine.raw_connection()
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            cursor.close()
            conn.close()
        except Exception as e:
            logger.warning("Database query failed for chunk: %s", e)
            continue

        if not rows:
            continue

        # Build Polars DataFrame from rows
        df = pl.DataFrame(
            {col: [row[i] for row in rows] for i, col in enumerate(columns)}
        )

        # Drop nulls
        df = df.drop_nulls(subset=["high", "low", "close", "volume"])

        if df.is_empty():
            continue

        # Group by (isin, trade_date) and extract numpy arrays
        for (isin, td), group in df.group_by(["isin", "trade_date"]):
            results[(isin, td)] = (
                group["high"].to_numpy().astype(np.float64),
                group["low"].to_numpy().astype(np.float64),
                group["close"].to_numpy().astype(np.float64),
                group["volume"].to_numpy().astype(np.int64),
            )

    return results
