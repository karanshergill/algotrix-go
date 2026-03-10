"""Value Area computation (VAH / VAL)."""


def compute_value_area(bucket_volumes, bucket_prices, poc_idx, bucket_size, value_area_pct):
    """Expand from POC until value_area_pct of total volume is captured.

    Returns:
        (vah, val)
    """
    total_volume = bucket_volumes.sum()
    va_target = total_volume * (value_area_pct / 100.0)
    va_volume = bucket_volumes[poc_idx]
    lo = hi = poc_idx

    while va_volume < va_target and (lo > 0 or hi < len(bucket_volumes) - 1):
        lo_vol = bucket_volumes[lo - 1] if lo > 0 else -1
        hi_vol = bucket_volumes[hi + 1] if hi < len(bucket_volumes) - 1 else -1
        if lo_vol >= hi_vol:
            lo -= 1
            va_volume += bucket_volumes[lo]
        else:
            hi += 1
            va_volume += bucket_volumes[hi]

    vah = float(bucket_prices[hi] + bucket_size / 2.0)
    val = float(bucket_prices[lo] - bucket_size / 2.0)
    return vah, val
