"""UTC timestamp utilities."""

from datetime import datetime, timezone


def to_utc_ns(date_str: str) -> int:
    """Convert 'YYYY-MM-DD' string to nanosecond UTC timestamp."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000_000


def utc_now_ns() -> int:
    """Current UTC time as nanosecond timestamp."""
    return int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
