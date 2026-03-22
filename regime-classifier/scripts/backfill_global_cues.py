#!/usr/bin/env python3
"""Backfill + daily fetch for global market cues (S&P 500, DXY, US 10Y yield).

Usage:
    # Full backfill from 2020-01-01 to today
    python3 backfill_global_cues.py

    # Incremental daily fetch (last 5 days to catch revisions)
    python3 backfill_global_cues.py --date 2026-03-21
"""

import argparse
import sys
from datetime import datetime, timedelta

import psycopg2
import yfinance as yf

DB_PARAMS = {
    "dbname": "atdb",
    "user": "me",
    "password": "algotrix",
    "host": "localhost",
}

SYMBOLS = ["^GSPC", "DX-Y.NYB", "^TNX"]

BACKFILL_START = "2020-01-01"


def upsert_rows(cur, rows):
    """Upsert rows into global_market_daily."""
    if not rows:
        return 0
    sql = """
        INSERT INTO global_market_daily (date, symbol, open, high, low, close, volume, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (date, symbol) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            fetched_at = EXCLUDED.fetched_at
    """
    count = 0
    for row in rows:
        cur.execute(sql, row)
        count += 1
    return count


def fetch_and_store(start_date, end_date):
    """Download data from yfinance and upsert into DB."""
    conn = psycopg2.connect(**DB_PARAMS)
    conn.autocommit = True
    cur = conn.cursor()

    total = 0
    for symbol in SYMBOLS:
        print(f"Fetching {symbol} from {start_date} to {end_date}...")
        try:
            df = yf.download(
                symbol,
                start=start_date,
                end=end_date,
                progress=False,
                auto_adjust=True,
            )
        except Exception as e:
            print(f"  ERROR downloading {symbol}: {e}")
            continue

        if df.empty:
            print(f"  No data for {symbol}")
            continue

        # yfinance >=1.x returns multi-level columns: ("Close", "^GSPC").
        # Flatten by dropping the ticker level.
        if isinstance(df.columns, __import__('pandas').MultiIndex):
            df.columns = df.columns.droplevel(1)

        rows = []
        for idx, row in df.iterrows():
            dt = idx.date() if hasattr(idx, "date") else idx
            close_val = float(row["Close"])
            open_val = float(row["Open"]) if row["Open"] == row["Open"] else None
            high_val = float(row["High"]) if row["High"] == row["High"] else None
            low_val = float(row["Low"]) if row["Low"] == row["Low"] else None
            vol = int(row["Volume"]) if "Volume" in row.index and row["Volume"] == row["Volume"] else None
            rows.append((dt, symbol, open_val, high_val, low_val, close_val, vol))

        inserted = upsert_rows(cur, rows)
        print(f"  {symbol}: {inserted} rows upserted")
        total += inserted

    cur.close()
    conn.close()
    print(f"Total: {total} rows upserted")
    return total


def main():
    parser = argparse.ArgumentParser(description="Backfill global market cues")
    parser.add_argument(
        "--date",
        type=str,
        help="Incremental fetch around this date (last 5 days). Format: YYYY-MM-DD",
    )
    args = parser.parse_args()

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d")
        start = (target - timedelta(days=5)).strftime("%Y-%m-%d")
        end = (target + timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"Incremental fetch: {start} to {end}")
    else:
        start = BACKFILL_START
        end = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"Full backfill: {start} to {end}")

    fetch_and_store(start, end)


if __name__ == "__main__":
    main()
