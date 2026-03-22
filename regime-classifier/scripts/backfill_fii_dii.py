#!/usr/bin/env python3
"""
FII/DII Participant Data Backfill Script
Downloads participant-wise OI and volume data from NSE archives.

Usage: python3 -u backfill_fii_dii.py [--from YYYY-MM-DD] [--to YYYY-MM-DD]
"""

import argparse
import csv
import datetime
import io
import logging
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
LOG_FILE = SCRIPT_DIR / "backfill_fii_dii.log"

DB_DSN = "dbname=atdb user=me password=algotrix host=localhost"

DEFAULT_START = datetime.date(2020, 1, 1)
DEFAULT_END = datetime.date(2026, 3, 21)

OI_URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{}.csv"
VOL_URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_vol_{}.csv"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

REQUEST_DELAY = 5.0
PROGRESS_INTERVAL = 50

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS nse_fii_dii_participant (
    date DATE PRIMARY KEY,
    -- FII
    fii_fut_idx_long BIGINT,
    fii_fut_idx_short BIGINT,
    fii_fut_stk_long BIGINT,
    fii_fut_stk_short BIGINT,
    fii_opt_idx_call_long BIGINT,
    fii_opt_idx_put_long BIGINT,
    fii_opt_idx_call_short BIGINT,
    fii_opt_idx_put_short BIGINT,
    fii_total_long BIGINT,
    fii_total_short BIGINT,
    -- DII
    dii_fut_idx_long BIGINT,
    dii_fut_idx_short BIGINT,
    dii_total_long BIGINT,
    dii_total_short BIGINT,
    -- Client
    client_total_long BIGINT,
    client_total_short BIGINT,
    -- Pro
    pro_total_long BIGINT,
    pro_total_short BIGINT,
    -- Meta
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);
"""

UPSERT_SQL = """
INSERT INTO nse_fii_dii_participant (
    date,
    fii_fut_idx_long, fii_fut_idx_short,
    fii_fut_stk_long, fii_fut_stk_short,
    fii_opt_idx_call_long, fii_opt_idx_put_long,
    fii_opt_idx_call_short, fii_opt_idx_put_short,
    fii_total_long, fii_total_short,
    dii_fut_idx_long, dii_fut_idx_short,
    dii_total_long, dii_total_short,
    client_total_long, client_total_short,
    pro_total_long, pro_total_short,
    fetched_at
) VALUES (
    %(date)s,
    %(fii_fut_idx_long)s, %(fii_fut_idx_short)s,
    %(fii_fut_stk_long)s, %(fii_fut_stk_short)s,
    %(fii_opt_idx_call_long)s, %(fii_opt_idx_put_long)s,
    %(fii_opt_idx_call_short)s, %(fii_opt_idx_put_short)s,
    %(fii_total_long)s, %(fii_total_short)s,
    %(dii_fut_idx_long)s, %(dii_fut_idx_short)s,
    %(dii_total_long)s, %(dii_total_short)s,
    %(client_total_long)s, %(client_total_short)s,
    %(pro_total_long)s, %(pro_total_short)s,
    NOW()
)
ON CONFLICT (date) DO UPDATE SET
    fii_fut_idx_long = EXCLUDED.fii_fut_idx_long,
    fii_fut_idx_short = EXCLUDED.fii_fut_idx_short,
    fii_fut_stk_long = EXCLUDED.fii_fut_stk_long,
    fii_fut_stk_short = EXCLUDED.fii_fut_stk_short,
    fii_opt_idx_call_long = EXCLUDED.fii_opt_idx_call_long,
    fii_opt_idx_put_long = EXCLUDED.fii_opt_idx_put_long,
    fii_opt_idx_call_short = EXCLUDED.fii_opt_idx_call_short,
    fii_opt_idx_put_short = EXCLUDED.fii_opt_idx_put_short,
    fii_total_long = EXCLUDED.fii_total_long,
    fii_total_short = EXCLUDED.fii_total_short,
    dii_fut_idx_long = EXCLUDED.dii_fut_idx_long,
    dii_fut_idx_short = EXCLUDED.dii_fut_idx_short,
    dii_total_long = EXCLUDED.dii_total_long,
    dii_total_short = EXCLUDED.dii_total_short,
    client_total_long = EXCLUDED.client_total_long,
    client_total_short = EXCLUDED.client_total_short,
    pro_total_long = EXCLUDED.pro_total_long,
    pro_total_short = EXCLUDED.pro_total_short,
    fetched_at = NOW()
;
"""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging():
    logger = logging.getLogger("fii_dii")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


log = setup_logging()

# ---------------------------------------------------------------------------
# Trading dates from nse_cm_bhavcopy
# ---------------------------------------------------------------------------
def get_trading_dates(conn, start, end):
    """Fetch trading dates from nse_cm_bhavcopy (guaranteed trading days)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT date FROM nse_cm_bhavcopy "
            "WHERE date >= %s AND date <= %s ORDER BY date",
            (start, end),
        )
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# CSV fetching and parsing
# ---------------------------------------------------------------------------
def fetch_csv(session, url, date_str):
    """Fetch a CSV from NSE. Returns parsed rows dict keyed by client type, or None."""
    try:
        resp = session.get(url.format(date_str), headers=HEADERS, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("HTTP error for %s: %s", date_str, e)
        return None

    text = resp.text.strip()
    if not text:
        return None

    lines = text.splitlines()
    # Row 1 is header text (skip), Row 2 is column headers, Row 3+ is data
    if len(lines) < 3:
        log.warning("Too few lines in CSV for %s", date_str)
        return None

    # Find the header row (contains "Client Type")
    header_idx = None
    for i, line in enumerate(lines):
        if "Client Type" in line:
            header_idx = i
            break

    if header_idx is None:
        log.warning("Could not find header row for %s", date_str)
        return None

    reader = csv.DictReader(lines[header_idx:])
    rows = {}
    for row in reader:
        # Normalize the client type key
        client_type = row.get("Client Type", "").strip().upper()
        if not client_type:
            # Try alternate key names
            for k in row:
                if "client" in k.lower() or "type" in k.lower():
                    client_type = row[k].strip().upper()
                    break
        if client_type:
            rows[client_type] = row

    return rows if rows else None


def safe_int(val):
    """Parse a value to int, handling commas and whitespace."""
    if val is None:
        return 0
    val = str(val).strip().replace(",", "").replace(" ", "")
    if not val or val == "-":
        return 0
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def get_col(row, *candidates):
    """Get a column value trying multiple candidate names."""
    for c in candidates:
        for key in row:
            if key.strip().lower() == c.lower():
                return row[key]
    return None


def extract_participant_row(row):
    """Extract relevant fields from a participant row."""
    return {
        "fut_idx_long": safe_int(get_col(row, "Future Index Long")),
        "fut_idx_short": safe_int(get_col(row, "Future Index Short")),
        "fut_stk_long": safe_int(get_col(row, "Future Stock Long")),
        "fut_stk_short": safe_int(get_col(row, "Future Stock Short")),
        "opt_idx_call_long": safe_int(get_col(row, "Option Index Call Long")),
        "opt_idx_put_long": safe_int(get_col(row, "Option Index Put Long")),
        "opt_idx_call_short": safe_int(get_col(row, "Option Index Call Short")),
        "opt_idx_put_short": safe_int(get_col(row, "Option Index Put Short")),
        "total_long": safe_int(get_col(row, "Total Long Contracts", "Total Long")),
        "total_short": safe_int(get_col(row, "Total Short Contracts", "Total Short")),
    }


def find_row(rows, *type_names):
    """Find a participant row by trying multiple type name variants."""
    for name in type_names:
        if name in rows:
            return rows[name]
    return None


# ---------------------------------------------------------------------------
# Main backfill
# ---------------------------------------------------------------------------
def backfill(start, end):
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True

    # Create table
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    log.info("Table nse_fii_dii_participant ready")

    # Get trading dates
    dates = get_trading_dates(conn, start, end)
    if not dates:
        log.error("No trading dates found in nse_cm_bhavcopy for range %s to %s", start, end)
        conn.close()
        return

    # Check which dates we already have
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date FROM nse_fii_dii_participant WHERE date >= %s AND date <= %s",
            (start, end),
        )
        existing = {row[0] for row in cur.fetchall()}

    pending = [d for d in dates if d not in existing]
    log.info(
        "Trading dates: %d total, %d already stored, %d to fetch",
        len(dates), len(existing), len(pending),
    )

    if not pending:
        log.info("Nothing to do — all dates already backfilled")
        conn.close()
        return

    session = requests.Session()
    # Warm up NSE session with a homepage hit
    try:
        session.get("https://www.nseindia.com/", headers=HEADERS, timeout=15)
        time.sleep(2)
    except Exception:
        pass

    inserted = 0
    skipped = 0
    errors = 0

    for i, dt in enumerate(pending):
        date_str = dt.strftime("%d%m%Y")

        # Fetch OI CSV
        oi_rows = fetch_csv(session, OI_URL, date_str)
        if oi_rows is None:
            log.debug("No OI data for %s, skipping", dt)
            skipped += 1
            if i > 0:
                time.sleep(REQUEST_DELAY)
            continue

        # Extract participant data from OI
        fii_row = find_row(oi_rows, "FII", "FPI", "FII/FPI")
        dii_row = find_row(oi_rows, "DII")
        client_row = find_row(oi_rows, "CLIENT", "CLIENTS")
        pro_row = find_row(oi_rows, "PRO", "PROPRIETARY", "PROP")

        if fii_row is None:
            log.warning("No FII row found for %s, available types: %s", dt, list(oi_rows.keys()))
            skipped += 1
            time.sleep(REQUEST_DELAY)
            continue

        fii = extract_participant_row(fii_row)
        dii = extract_participant_row(dii_row) if dii_row else {}
        client = extract_participant_row(client_row) if client_row else {}
        pro = extract_participant_row(pro_row) if pro_row else {}

        params = {
            "date": dt,
            "fii_fut_idx_long": fii.get("fut_idx_long", 0),
            "fii_fut_idx_short": fii.get("fut_idx_short", 0),
            "fii_fut_stk_long": fii.get("fut_stk_long", 0),
            "fii_fut_stk_short": fii.get("fut_stk_short", 0),
            "fii_opt_idx_call_long": fii.get("opt_idx_call_long", 0),
            "fii_opt_idx_put_long": fii.get("opt_idx_put_long", 0),
            "fii_opt_idx_call_short": fii.get("opt_idx_call_short", 0),
            "fii_opt_idx_put_short": fii.get("opt_idx_put_short", 0),
            "fii_total_long": fii.get("total_long", 0),
            "fii_total_short": fii.get("total_short", 0),
            "dii_fut_idx_long": dii.get("fut_idx_long", 0),
            "dii_fut_idx_short": dii.get("fut_idx_short", 0),
            "dii_total_long": dii.get("total_long", 0),
            "dii_total_short": dii.get("total_short", 0),
            "client_total_long": client.get("total_long", 0),
            "client_total_short": client.get("total_short", 0),
            "pro_total_long": pro.get("total_long", 0),
            "pro_total_short": pro.get("total_short", 0),
        }

        try:
            with conn.cursor() as cur:
                cur.execute(UPSERT_SQL, params)
            inserted += 1
        except Exception as e:
            log.error("DB error for %s: %s", dt, e)
            errors += 1

        if (i + 1) % PROGRESS_INTERVAL == 0:
            log.info(
                "Progress: %d/%d (inserted=%d, skipped=%d, errors=%d)",
                i + 1, len(pending), inserted, skipped, errors,
            )

        time.sleep(REQUEST_DELAY)

    log.info(
        "Done. Total=%d, Inserted=%d, Skipped=%d, Errors=%d",
        len(pending), inserted, skipped, errors,
    )
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Backfill FII/DII participant data from NSE")
    parser.add_argument("--from", dest="from_date", type=str, default=None,
                        help="Start date YYYY-MM-DD (default: 2020-01-01)")
    parser.add_argument("--to", dest="to_date", type=str, default=None,
                        help="End date YYYY-MM-DD (default: 2026-03-21)")
    args = parser.parse_args()

    start = datetime.datetime.strptime(args.from_date, "%Y-%m-%d").date() if args.from_date else DEFAULT_START
    end = datetime.datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else DEFAULT_END

    log.info("Backfilling FII/DII participant data from %s to %s", start, end)
    backfill(start, end)


if __name__ == "__main__":
    main()
