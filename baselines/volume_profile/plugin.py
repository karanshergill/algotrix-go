"""Volume profile baseline plugin — rewritten with range-overlap allocation."""

from datetime import datetime, timedelta, timezone

from baselines.baseline_plugin import BaselinePlugin, questdb_write_ilp
from baselines.shared.filters import filter_outliers_mad
from baselines.volume_profile.allocate import allocate_volume
from baselines.volume_profile.buckets import build_buckets
from baselines.volume_profile.hvn_lvn import detect_hvn_lvn
from baselines.volume_profile.output import build_output
from baselines.volume_profile.poc import find_poc
from baselines.volume_profile.value_area import compute_value_area
from db.fetch_ohlcv import fetch_ohlcv
from utils import to_utc_ns, get_isins


class VolumeProfilePlugin(BaselinePlugin):
    """Compute volume profile per (isin, trading_day).

    Uses range-overlap volume allocation across price buckets with
    MAD-based outlier filtering and percentile-based HVN/LVN detection.
    """

    name = "volume_profile"
    description = "Price-volume distribution profile per stock per day"
    dependencies = []

    def validate_config(self):
        required = ["table", "source", "bucket_size", "value_area_pct",
                     "hvn_percentile", "lvn_percentile", "outlier_mad_k",
                     "lookback_days"]
        for key in required:
            if key not in self.plugin_cfg:
                raise ValueError(f"volume_profile: missing config key '{key}'")

    def compute(self):
        pcfg = self.plugin_cfg
        bucket_size = float(pcfg["bucket_size"])
        value_area_pct = float(pcfg["value_area_pct"])
        hvn_percentile = float(pcfg["hvn_percentile"])
        lvn_percentile = float(pcfg["lvn_percentile"])
        outlier_k = float(pcfg["outlier_mad_k"])
        lookback_days = pcfg["lookback_days"]
        source_key = pcfg["source"]

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days + 10)

        source_table = self.cfg["sources"][source_key]
        all_isins = get_isins(self.cfg, source_table,
                              start_date.isoformat(), end_date.isoformat())
        if not all_isins:
            return []

        grouped = fetch_ohlcv(self.cfg, source_key, all_isins,
                              start_date.isoformat(), end_date.isoformat())

        results = []
        for (isin, trade_date), (highs, lows, closes, volumes) in grouped.items():
            highs, lows, closes, volumes = filter_outliers_mad(
                highs, lows, closes, volumes, k=outlier_k)

            if len(closes) == 0:
                continue

            total_volume = float(volumes.sum())
            if total_volume == 0:
                continue

            price_min, price_max, n_buckets, bucket_prices = build_buckets(
                lows, highs, bucket_size)

            bucket_volumes = allocate_volume(
                highs, lows, closes, volumes,
                price_min, bucket_size, n_buckets, self.cfg)

            poc_idx, poc_price = find_poc(bucket_volumes, bucket_prices)

            vah, val = compute_value_area(
                bucket_volumes, bucket_prices, poc_idx,
                bucket_size, value_area_pct)

            hvn_levels, lvn_levels, hvn_count, lvn_count = detect_hvn_lvn(
                bucket_volumes, bucket_prices, hvn_percentile, lvn_percentile)

            ts_ns = to_utc_ns(trade_date)

            results.append(build_output(
                isin, trade_date, bucket_volumes, bucket_prices, total_volume,
                poc_price, vah, val, hvn_levels, lvn_levels,
                hvn_count, lvn_count, n_buckets, ts_ns))

        return results

    def store(self, results):
        if not results:
            return 0
        table = self.plugin_cfg["table"]
        return questdb_write_ilp(
            self.cfg, table, results,
            symbols=["isin"],
            timestamps=["timestamp"],
        )
