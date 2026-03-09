"""Write baseline computation results to QuestDB via SQL."""

import logging
from typing import Any, Dict, List

import pandas as pd

from db.conns.py_conn import get_questdb_engine

logger = logging.getLogger(__name__)


def write_baseline(
    table: str,
    results: List[Dict[str, Any]],
    symbol_columns: List[str],
    timestamp_column: str = "timestamp",
) -> int:
    """Write baseline results to a QuestDB table via SQL INSERT.

    Args:
        table: Target table name.
        results: List of result dicts (one per row).
        symbol_columns: Column names that are SYMBOL type in QuestDB.
        timestamp_column: Column used as designated timestamp.

    Returns:
        Number of rows written.
    """
    if not results:
        return 0

    engine = get_questdb_engine()
    df = pd.DataFrame(results)

    # Convert nanosecond timestamps to datetime for QuestDB
    for col in df.columns:
        if col == timestamp_column or col.endswith("_date"):
            # Columns with nanosecond int timestamps
            if df[col].dtype in ("int64", "float64"):
                df[col] = pd.to_datetime(df[col], unit="ns", utc=True)

    try:
        # Use multi-row INSERT via pandas to_sql
        # method='multi' batches inserts for performance
        # if_exists='append' adds to existing table
        df.to_sql(
            table,
            con=engine,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=1000,
        )
        row_count = len(df)
        logger.info("Wrote %d rows to %s", row_count, table)
        return row_count
    except Exception as e:
        logger.error("Failed to write to %s: %s", table, e)
        raise
