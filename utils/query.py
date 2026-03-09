"""QuestDB query utilities for ISIN chunking and WHERE clause building."""

ISIN_CHUNK_SIZE = 50


def get_isins(cfg, source_table, start_date, end_date):
    """Fetch distinct ISINs from a source table within date range, respecting _symbols_filter."""
    from baselines.baseline_plugin import questdb_query

    where_parts = [
        f"timestamp >= '{start_date}'",
        f"timestamp <= '{end_date}T23:59:59.999999Z'",
    ]
    symbol_filter = cfg.get("_symbols_filter")
    if symbol_filter:
        isin_list = ",".join(f"'{s}'" for s in symbol_filter)
        where_parts.append(f"isin IN ({isin_list})")

    where_clause = " AND ".join(where_parts)
    sql = f"SELECT DISTINCT isin FROM {source_table} WHERE {where_clause}"
    rows = questdb_query(cfg, sql)
    return [r["isin"] for r in rows]


def chunked(items, size=ISIN_CHUNK_SIZE):
    """Yield successive chunks of `size` from `items`."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


def isin_where_clause(isins, start_date, end_date):
    """Build WHERE clause for a chunk of ISINs within date range."""
    isin_list = ",".join(f"'{s}'" for s in isins)
    return (
        f"isin IN ({isin_list}) "
        f"AND timestamp >= '{start_date}' "
        f"AND timestamp <= '{end_date}T23:59:59.999999Z'"
    )
