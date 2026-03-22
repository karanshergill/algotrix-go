#!/usr/bin/env python3
"""Backfill NSE IX settlement prices + combined OI via the Go pipeline binary.

Iterates trading dates from Jul 2023 → present, running the Go market-data CLI
for both nseix_settlement and nseix_combined_oi feeds per date.

Usage: python3 -u backfill_nseix.py [--from YYYY-MM-DD] [--to YYYY-MM-DD]
"""

import argparse
import datetime
import logging
import subprocess
import sys
import time

import psycopg2

DB_DSN = "dbname=atdb user=me password=algotrix host=localhost"
BINARY = "/tmp/algotrix-test"
ENGINE_DIR = "/home/me/projects/algotrix-go/engine"
DEFAULT_START = datetime.date(2023, 7, 14)  # GIFT Nifty launch
DELAY_BETWEEN_DATES = 3  # seconds
PROGRESS_INTERVAL = 50

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/nseix_backfill.log", mode="a"),
    ],
)
log = logging.getLogger("nseix_backfill")


def get_trading_dates(start, end):
    """Get trading dates from nse_cm_bhavcopy."""
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT date FROM nse_cm_bhavcopy WHERE date >= %s AND date <= %s ORDER BY date",
        (start, end),
    )
    dates = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return dates


def get_existing_dates():
    """Get dates already in nseix_settlement_prices."""
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT date FROM nseix_settlement_prices")
    dates = {row[0] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return dates


def build_binary():
    """Build the Go binary."""
    log.info("Building Go binary...")
    result = subprocess.run(
        ["go", "build", "-o", BINARY, "."],
        cwd=ENGINE_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("Build failed: %s", result.stderr)
        sys.exit(1)
    log.info("Binary built: %s", BINARY)


def run_feeds(date_str):
    """Run both NSE IX feeds for a single date. Returns (ok, skipped, failed)."""
    result = subprocess.run(
        [BINARY, "market-data", "--date", date_str, "--feed", "nseix_settlement,nseix_combined_oi"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=ENGINE_DIR,
    )
    output = result.stdout + result.stderr

    settle_ok = "FEED OK: nseix_settlement" in output
    oi_ok = "FEED OK: nseix_combined_oi" in output
    settle_skip = "FEED SKIPPED: nseix_settlement" in output
    oi_skip = "FEED SKIPPED: nseix_combined_oi" in output

    if settle_ok and oi_ok:
        return "ok", output
    elif settle_skip or oi_skip:
        return "skip", output
    elif settle_ok and oi_skip:
        return "partial", output
    elif settle_skip and oi_ok:
        return "partial", output
    else:
        return "fail", output


def main():
    parser = argparse.ArgumentParser(description="Backfill NSE IX data")
    parser.add_argument("--from", dest="from_date", type=str, default=None)
    parser.add_argument("--to", dest="to_date", type=str, default=None)
    args = parser.parse_args()

    start = datetime.datetime.strptime(args.from_date, "%Y-%m-%d").date() if args.from_date else DEFAULT_START
    end = datetime.datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else datetime.date.today()

    build_binary()

    all_dates = get_trading_dates(start, end)
    existing = get_existing_dates()
    pending = [d for d in all_dates if d not in existing]

    log.info("Trading dates: %d total, %d already stored, %d to fetch", len(all_dates), len(existing), len(pending))

    if not pending:
        log.info("Nothing to do — all dates already backfilled")
        return

    ok = skip = fail = partial = 0

    for i, dt in enumerate(pending):
        date_str = dt.strftime("%Y-%m-%d")
        status, output = run_feeds(date_str)

        if status == "ok":
            ok += 1
        elif status == "skip":
            skip += 1
        elif status == "partial":
            partial += 1
        else:
            fail += 1
            log.warning("FAIL on %s: %s", date_str, output.strip().split("\n")[-3:])

        if (i + 1) % PROGRESS_INTERVAL == 0:
            log.info(
                "Progress: %d/%d (ok=%d, skip=%d, partial=%d, fail=%d) last=%s",
                i + 1, len(pending), ok, skip, partial, fail, date_str,
            )

        time.sleep(DELAY_BETWEEN_DATES)

    log.info("DONE. Total=%d OK=%d Skip=%d Partial=%d Fail=%d", len(pending), ok, skip, partial, fail)


if __name__ == "__main__":
    main()
