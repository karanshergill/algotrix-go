"""Fetch distinct ISINs from QuestDB source tables."""

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from db.conns.py_conn import get_questdb_engine

logger = logging.getLogger(__name__)


def fetch_isins(
    cfg: Dict[str, Any],
    source_table: str,
    start_date: str,
    end_date: str,
    symbols_filter: Optional[List[str]] = None,
) -> List[str]:
    """Fetch distinct ISINs from a source table within date range.

    Args:
        cfg: Baseline config dict.
        source_table: Resolved table name (e.g. "nse_cm_ohlcv_5s").
        start_date: Start date string (YYYY-MM-DD).
        end_date: End date string (YYYY-MM-DD).
        symbols_filter: Optional list of ISINs to restrict to.

    Returns:
        List of ISIN strings.
    """
    engine = get_questdb_engine()

    where_parts = [
        f"timestamp >= '{start_date}'",
        f"timestamp <= '{end_date}T23:59:59.999999Z'",
    ]
    if symbols_filter:
        isin_list = ",".join(f"'{s}'" for s in symbols_filter)
        where_parts.append(f"isin IN ({isin_list})")

    where_clause = " AND ".join(where_parts)
    sql = f"SELECT DISTINCT isin FROM {source_table} WHERE {where_clause}"

    try:
        df = pd.read_sql_query(sql, con=engine)
    except Exception as e:
        logger.error("Failed to fetch ISINs from %s: %s", source_table, e)
        return []

    if df.empty:
        return []

    return df["isin"].tolist()
