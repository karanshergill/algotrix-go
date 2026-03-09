"""Output row builder for volume profile results."""

import json


def build_output(isin, trade_date, bucket_volumes, bucket_prices, total_volume,
                 poc, vah, val, hvn_levels, lvn_levels, hvn_count, lvn_count,
                 n_buckets, ts_ns):
    """Build a result dict matching the output schema.

    Returns:
        dict ready for questdb_write_ilp.
    """
    price_buckets = []
    for i in range(len(bucket_volumes)):
        bv = bucket_volumes[i]
        if bv > 0:
            price_buckets.append({
                "price": round(float(bucket_prices[i]), 2),
                "volume": round(float(bv), 2),
                "pct": round(float(bv) / total_volume * 100, 2),
            })

    return {
        "isin": isin,
        "trade_date": ts_ns,
        "poc": round(poc, 2),
        "vah": round(vah, 2),
        "val": round(val, 2),
        "total_volume": int(total_volume),
        "bucket_count": n_buckets,
        "hvn_count": hvn_count,
        "lvn_count": lvn_count,
        "price_buckets": json.dumps(price_buckets),
        "hvn_levels": json.dumps(hvn_levels),
        "lvn_levels": json.dumps(lvn_levels),
        "timestamp": ts_ns,
    }
