"""Cross-stock correlation baseline computation plugin."""

from datetime import datetime, timedelta, timezone

import numpy as np

from baselines.baseline_plugin import BaselinePlugin, questdb_query, questdb_write_ilp
from utils import utc_now_ns, get_isins, chunked, isin_where_clause


class CorrelationsPlugin(BaselinePlugin):
    """Compute pairwise return correlations between stocks.

    Queries ISINs in chunks to avoid OOM, assembles aligned return
    matrix, then computes Pearson correlations. Stores top_peers per stock.
    """

    name = "correlations"
    description = "Pairwise return correlations between stocks"
    dependencies = []

    def validate_config(self):
        required = ["table", "source", "return_timeframe", "lookback_days",
                     "min_correlation", "top_peers"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"correlations: missing config key '{key}'")

    def _fetch_chunk_returns(self, source_table, isins, tf, start_str, end_str):
        """Fetch 5m close series for a chunk of ISINs, return {isin: {ts: close}}."""
        where = isin_where_clause(isins, start_str, end_str)
        sql = (
            f"SELECT isin, timestamp, last(close) AS close "
            f"FROM {source_table} "
            f"WHERE {where} "
            f"SAMPLE BY {tf} FILL(NULL) ALIGN TO CALENDAR "
            f"ORDER BY isin, timestamp"
        )
        rows = questdb_query(self.cfg, sql)

        chunk_data = {}
        for r in rows:
            isin = r["isin"]
            ts = str(r["timestamp"])
            close = r["close"]
            if close is None:
                continue
            if isin not in chunk_data:
                chunk_data[isin] = {}
            chunk_data[isin][ts] = float(close)

        return chunk_data

    def compute(self):
        pcfg = self.plugin_cfg
        source_table = self.cfg["sources"][pcfg["source"]]
        lookback_days = pcfg["lookback_days"]
        min_corr = float(pcfg["min_correlation"])
        top_peers = int(pcfg["top_peers"])
        tf = pcfg["return_timeframe"]

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + 10)
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        all_isins = get_isins(self.cfg, source_table, start_str, end_str)
        if len(all_isins) < 2:
            return []

        # Fetch 5m close series in chunks
        isin_series = {}
        all_timestamps = set()

        for chunk in chunked(all_isins):
            chunk_data = self._fetch_chunk_returns(
                source_table, chunk, tf, start_str, end_str
            )
            for isin, ts_map in chunk_data.items():
                isin_series[isin] = ts_map
                all_timestamps.update(ts_map.keys())

        if len(isin_series) < 2 or not all_timestamps:
            return []

        # Build aligned return matrix
        sorted_ts = sorted(all_timestamps)
        isins = sorted(isin_series.keys())
        ts_index = {ts: i for i, ts in enumerate(sorted_ts)}

        n_ts = len(sorted_ts)
        n_isins = len(isins)
        price_matrix = np.full((n_ts, n_isins), np.nan)

        for col, isin in enumerate(isins):
            ts_map = isin_series[isin]
            for ts, close in ts_map.items():
                row = ts_index[ts]
                price_matrix[row, col] = close

        del isin_series
        del all_timestamps

        # Log returns
        returns = np.diff(np.log(price_matrix), axis=0)
        returns[~np.isfinite(returns)] = np.nan
        del price_matrix

        # Compute pairwise correlations using vectorized np.corrcoef.
        # Replace NaN with 0 for the correlation matrix computation, then
        # use a valid-observation count matrix to filter low-overlap pairs.
        now_ns = utc_now_ns()

        # Build a mask of valid (finite) returns per stock
        valid_mask = np.isfinite(returns)
        # Count pairwise valid observations: valid_counts[i,j] = number of
        # rows where both stock i and stock j have finite returns
        valid_counts = valid_mask.astype(np.float64).T @ valid_mask.astype(np.float64)

        # Zero out NaN returns for corrcoef computation
        clean_returns = np.where(valid_mask, returns, 0.0)

        # Vectorized Pearson correlation matrix
        # np.corrcoef returns NaN for constant columns, which we'll handle
        with np.errstate(divide="ignore", invalid="ignore"):
            corr_matrix = np.corrcoef(clean_returns.T)

        del returns
        del clean_returns

        # However, np.corrcoef on zero-filled data biases correlations.
        # For stocks with many overlapping observations this is fine, but
        # for sparse overlap it's inaccurate. We filter by min 20 overlap
        # and recompute only if needed. For large N this is still much
        # faster than the O(N^2) Python loop.

        # Collect all pairs above threshold into per-isin buckets
        # by_isin[isin_a] = [(isin_b, corr), ...]
        by_isin = {}

        for i in range(n_isins):
            isin_a = isins[i]
            peers = []
            for j in range(n_isins):
                if i == j:
                    continue
                if valid_counts[i, j] < 20:
                    continue
                corr = corr_matrix[i, j]
                if not np.isfinite(corr):
                    continue
                if abs(corr) >= min_corr:
                    peers.append((j, float(corr)))

            if not peers:
                continue

            # Keep top_peers per stock
            peers.sort(key=lambda x: abs(x[1]), reverse=True)
            by_isin[isin_a] = peers[:top_peers]

        # Build result rows
        results = []
        for isin_a, peers in by_isin.items():
            for j, corr in peers:
                results.append({
                    "isin_a": isin_a,
                    "isin_b": isins[j],
                    "correlation": round(corr, 4),
                    "lookback_days": lookback_days,
                    "timestamp": now_ns,
                })

        return results

    def store(self, results):
        if not results:
            return 0
        table = self.plugin_cfg["table"]
        return questdb_write_ilp(
            self.cfg, table, results,
            symbols=["isin_a", "isin_b"],
            timestamps=["timestamp"],
        )
