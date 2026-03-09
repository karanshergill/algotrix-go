"""Tick size lookup from config tick bands."""


def get_tick_size(cfg, price):
    """Return the tick size for a given price based on configured tick bands."""
    bands = cfg["tick_bands"]
    for band in bands:
        max_price = band["max_price"]
        if max_price is None or price < max_price:
            return float(band["tick_size"])
    return float(bands[-1]["tick_size"])
