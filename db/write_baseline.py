"""Write baseline computation results to QuestDB via raw SQL INSERT."""

import logging
from typing import Any, Dict, List

from db.conns.py_conn import get_questdb_engine

logger = logging.getLogger(__name__)


def write_baseline(
    table: str,
    results: List[Dict[str, Any]],
    symbol_columns: List[str],
    timestamp_column: str = "timestamp",
) -> int:
    """Write baseline results to a QuestDB table via SQL INSERT.

    Uses raw psycopg2 connection since QuestDB's Postgres wire protocol
    doesn't support SQLAlchemy ORM or pg_catalog queries.

    Timestamp columns are kept as nanosecond integers — converted to
    microseconds for QuestDB's Postgres wire protocol.

    Args:
        table: Target table name.
        results: List of result dicts (one per row).
        symbol_columns: Column names that are SYMBOL type (for reference).
        timestamp_column: Column used as designated timestamp.

    Returns:
        Number of rows written.
    """
    if not results:
        return 0

    columns = list(results[0].keys())
    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    # Identify timestamp columns (ns int → µs int for QuestDB pg wire)
    ts_cols = set()
    for col in columns:
        if col == timestamp_column or col.endswith("_date"):
            ts_cols.add(col)

    # Build batch of tuples
    batch = []
    for row in results:
        values = []
        for col in columns:
            val = row[col]
            if col in ts_cols and isinstance(val, (int, float)):
                # QuestDB Postgres wire expects microseconds
                val = int(val) // 1000
            values.append(val)
        batch.append(tuple(values))

    engine = get_questdb_engine()
    conn = engine.raw_connection()
    rows_written = 0
    try:
        cursor = conn.cursor()
        for i in range(0, len(batch), 1000):
            chunk = batch[i:i + 1000]
            cursor.executemany(insert_sql, chunk)
            rows_written += len(chunk)
        conn.commit()
        logger.info("Wrote %d rows to %s", rows_written, table)
    except Exception as e:
        conn.rollback()
        logger.error("Failed to write to %s: %s", table, e)
        raise
    finally:
        cursor.close()
        conn.close()

    return rows_written
