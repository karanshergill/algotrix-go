#!/usr/bin/env python3
"""
NSE Historical Data Backfill Script
Downloads and ingests NSE data from Jan 2020 to Aug 2025 into PostgreSQL.
Disposable, one-time use. Does NOT modify the Go pipeline.

Usage: python3 -u backfill_nse_history.py [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--feed cm|idx|fo|all]
"""

import csv
import datetime
import hashlib
import io
import json
import logging
import os
import sys
import time
import traceback
import zipfile
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
CHECKPOINT_FILE = SCRIPT_DIR / "backfill_checkpoint.json"
LOG_FILE = SCRIPT_DIR / "backfill.log"
REPORT_FILE = SCRIPT_DIR / "backfill_report.txt"

DB_DSN = "dbname=atdb user=me password=algotrix host=localhost"

DEFAULT_START = datetime.date(2020, 1, 1)
DEFAULT_END = datetime.date(2025, 8, 31)

FORMAT_SWITCH_DATE = datetime.date(2024, 1, 1)  # new format from this date

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

REQUEST_DELAY = 2.0  # seconds between requests
BACKOFF_BASE = 5.0
MAX_RETRIES = 3
PROGRESS_INTERVAL = 50  # print progress every N dates

MONTHS_UPPER = [
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging():
    logger = logging.getLogger("backfill")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler
    fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


log = setup_logging()

# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------
def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {"cm": {}, "idx": {}, "fo": {}}


def save_checkpoint(ckpt):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(ckpt, f)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def fetch_url(url, retries=MAX_RETRIES):
    """Fetch URL with retry/backoff. Returns (bytes, status) or (None, status)."""
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=30)
            log.debug(f"HTTP {resp.status_code} {url} ({len(resp.content)} bytes)")

            if resp.status_code == 200:
                return resp.content, 200
            elif resp.status_code == 404:
                return None, 404
            elif resp.status_code in (429, 500, 502, 503, 504):
                wait = BACKOFF_BASE * (2 ** attempt)
                log.warning(f"HTTP {resp.status_code} for {url}, backing off {wait}s (attempt {attempt+1})")
                time.sleep(wait)
                continue
            else:
                log.warning(f"HTTP {resp.status_code} for {url}, skipping")
                return None, resp.status_code
        except requests.RequestException as e:
            wait = BACKOFF_BASE * (2 ** attempt)
            log.warning(f"Request error for {url}: {e}, backing off {wait}s (attempt {attempt+1})")
            time.sleep(wait)

    log.error(f"Failed after {retries} retries: {url}")
    return None, -1


def extract_csv_from_zip(data, filename_hint=None):
    """Extract first CSV from zip bytes."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        # Pick the CSV file
        csv_name = None
        for n in names:
            if n.lower().endswith(".csv"):
                csv_name = n
                break
        if csv_name is None and names:
            csv_name = names[0]
        if csv_name is None:
            return None
        return zf.read(csv_name).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def trading_dates(start, end):
    """Yield weekday dates from start to end inclusive."""
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            yield d
        d += datetime.timedelta(days=1)


def date_mmm(d):
    """Return uppercase 3-letter month: JAN, FEB, ..."""
    return MONTHS_UPPER[d.month - 1]


def format_old_date(d):
    """DD-MMM-YYYY uppercase: 02JAN2020"""
    return f"{d.day:02d}{date_mmm(d)}{d.year}"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def safe_float(val):
    if val is None:
        return None
    val = str(val).strip()
    if val in ("", "-", "nan", "NaN", "NA", "N/A", " -"):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def safe_int(val):
    if val is None:
        return None
    val = str(val).strip()
    if val in ("", "-", "nan", "NaN", "NA", "N/A", " -"):
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def parse_date_ddmmmyyyy(s):
    """Parse DD-MMM-YYYY like 02-JAN-2020 or 02-Jan-2020."""
    s = s.strip()
    return datetime.datetime.strptime(s, "%d-%b-%Y").date()


def parse_date_ddmmyyyy(s):
    """Parse DD-MM-YYYY like 02-01-2020."""
    s = s.strip()
    return datetime.datetime.strptime(s, "%d-%m-%Y").date()


def generate_fo_instrument_id(date_val, instrument, symbol, expiry, strike, option_type):
    """Generate deterministic integer ID from composite key."""
    key = f"{date_val}|{instrument}|{symbol}|{expiry}|{strike}|{option_type}"
    h = hashlib.md5(key.encode()).hexdigest()
    # Use first 8 hex chars → 32-bit integer (positive)
    return int(h[:8], 16) & 0x7FFFFFFF


# ---------------------------------------------------------------------------
# CM Bhavcopy
# ---------------------------------------------------------------------------
def url_cm_old(d):
    mmm = date_mmm(d)
    return (
        f"https://nsearchives.nseindia.com/content/historical/EQUITIES/"
        f"{d.year}/{mmm}/cm{format_old_date(d)}bhav.csv.zip"
    )


def url_cm_new(d):
    return (
        f"https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv.zip"
    )


def parse_cm_old(csv_text, target_date):
    """Parse old CM bhavcopy CSV. Returns list of tuples."""
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for r in reader:
        # Strip whitespace from keys
        r = {k.strip(): v.strip() if v else v for k, v in r.items()}
        if r.get("SERIES") != "EQ":
            continue
        isin = r.get("ISIN", "").strip()
        if not isin:
            continue
        rows.append((
            isin,
            target_date,
            safe_float(r.get("OPEN")),
            safe_float(r.get("HIGH")),
            safe_float(r.get("LOW")),
            safe_float(r.get("CLOSE")),
            safe_float(r.get("LAST")),
            safe_float(r.get("PREVCLOSE")),
            safe_int(r.get("TOTTRDQTY")),
            safe_float(r.get("TOTTRDVAL")),
            safe_int(r.get("TOTALTRADES")),
        ))
    return rows


def parse_cm_new(csv_text, target_date):
    """Parse new CM bhavcopy CSV. Returns list of tuples."""
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for r in reader:
        r = {k.strip(): v.strip() if v else v for k, v in r.items()}
        if r.get("SctySrs") != "EQ":
            continue
        isin = r.get("ISIN", "").strip()
        if not isin:
            continue
        rows.append((
            isin,
            target_date,
            safe_float(r.get("OpnPric")),
            safe_float(r.get("HghPric")),
            safe_float(r.get("LwPric")),
            safe_float(r.get("ClsPric")),
            safe_float(r.get("LastPric")),
            safe_float(r.get("PrvsClsgPric")),
            safe_int(r.get("TtlTradgVol")),
            safe_float(r.get("TtlTrfVal")),
            safe_int(r.get("TtlNbOfTxsExctd")),
        ))
    return rows


CM_INSERT = """
    INSERT INTO nse_cm_bhavcopy (isin, date, open, high, low, close, last_price, prev_close, volume, traded_value, num_trades)
    VALUES %s
    ON CONFLICT (isin, date) DO NOTHING
"""


def process_cm(d, conn):
    """Fetch and insert CM bhavcopy for date d. Returns (parsed, inserted)."""
    if d < FORMAT_SWITCH_DATE:
        url = url_cm_old(d)
    else:
        url = url_cm_new(d)

    data, status = fetch_url(url)
    if status == 404:
        return -1, 0  # holiday
    if data is None:
        return -2, 0  # error

    csv_text = extract_csv_from_zip(data)
    if csv_text is None:
        log.error(f"CM {d}: could not extract CSV from zip")
        return -2, 0

    if d < FORMAT_SWITCH_DATE:
        rows = parse_cm_old(csv_text, d)
    else:
        rows = parse_cm_new(csv_text, d)

    parsed = len(rows)
    if parsed == 0:
        return 0, 0

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, CM_INSERT, rows, page_size=500)
        inserted = cur.rowcount
    conn.commit()
    return parsed, inserted


# ---------------------------------------------------------------------------
# Indices Daily
# ---------------------------------------------------------------------------
def url_idx(d):
    return (
        f"https://nsearchives.nseindia.com/content/indices/"
        f"ind_close_all_{d.strftime('%d%m%Y')}.csv"
    )


def parse_idx(csv_text, target_date):
    """Parse indices CSV. Returns list of tuples."""
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for r in reader:
        r = {k.strip(): v.strip() if v else v for k, v in r.items()}
        index_name = r.get("Index Name", "").strip()
        if not index_name:
            continue
        rows.append((
            target_date,
            index_name,
            safe_float(r.get("Open Index Value")),
            safe_float(r.get("High Index Value")),
            safe_float(r.get("Low Index Value")),
            safe_float(r.get("Closing Index Value")),
            safe_int(r.get("Volume")),
            safe_float(r.get("Turnover (Rs. Cr.)")),
            safe_float(r.get("P/E")),
            safe_float(r.get("P/B")),
            safe_float(r.get("Div Yield")),
        ))
    return rows


IDX_INSERT = """
    INSERT INTO nse_indices_daily (date, index, open, high, low, close, volume, turnover, pe, pb, div_yield)
    VALUES %s
    ON CONFLICT (date, index) DO NOTHING
"""


def process_idx(d, conn):
    """Fetch and insert indices for date d. Returns (parsed, inserted)."""
    url = url_idx(d)
    data, status = fetch_url(url)
    if status == 404:
        return -1, 0
    if data is None:
        return -2, 0

    csv_text = data.decode("utf-8", errors="replace")
    rows = parse_idx(csv_text, d)
    parsed = len(rows)
    if parsed == 0:
        return 0, 0

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, IDX_INSERT, rows, page_size=500)
        inserted = cur.rowcount
    conn.commit()
    return parsed, inserted


# ---------------------------------------------------------------------------
# F&O Bhavcopy
# ---------------------------------------------------------------------------
def url_fo_old(d):
    mmm = date_mmm(d)
    return (
        f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
        f"{d.year}/{mmm}/fo{format_old_date(d)}bhav.csv.zip"
    )


def url_fo_new(d):
    return (
        f"https://nsearchives.nseindia.com/content/fo/"
        f"BhavCopy_NSE_FO_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv.zip"
    )


def parse_fo_old(csv_text, target_date):
    """Parse old F&O bhavcopy CSV. Returns list of tuples matching nse_fo_bhavcopy columns."""
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for r in reader:
        r = {k.strip(): v.strip() if v else v for k, v in r.items()}
        instrument = r.get("INSTRUMENT", "").strip()
        symbol = r.get("SYMBOL", "").strip()
        if not instrument or not symbol:
            continue

        expiry_str = r.get("EXPIRY_DT", "").strip()
        strike_val = safe_float(r.get("STRIKE_PR"))
        option_type = r.get("OPTION_TYP", "").strip()

        try:
            expiry_date = parse_date_ddmmmyyyy(expiry_str) if expiry_str else None
        except ValueError:
            expiry_date = None

        instrument_id = generate_fo_instrument_id(
            target_date, instrument, symbol, expiry_str, str(strike_val), option_type
        )

        # turnover: VAL_INLAKH → convert lakh to crore (/100)
        val_in_lakh = safe_float(r.get("VAL_INLAKH"))
        turnover = val_in_lakh / 100.0 if val_in_lakh is not None else None

        rows.append((
            target_date,        # date
            target_date,        # biz_date (same as date for old format)
            "NFO-FO",           # segment
            "NSE",              # source
            instrument,         # instrument_type
            instrument_id,      # instrument_id
            None,               # isin (not in old format)
            symbol,             # symbol
            None,               # series
            expiry_date,        # expiry
            None,               # actual_expiry
            strike_val,         # strike
            option_type if option_type else None,  # option_type
            f"{instrument}-{symbol}",  # instrument_name
            safe_float(r.get("OPEN")),
            safe_float(r.get("HIGH")),
            safe_float(r.get("LOW")),
            safe_float(r.get("CLOSE")),
            None,               # last (not in old format)
            None,               # prev_close (not in old format)
            None,               # underlying (not in old format)
            safe_float(r.get("SETTLE_PR")),  # settlement
            safe_int(r.get("OPEN_INT")),     # oi
            safe_int(r.get("CHG_IN_OI")),    # oi_change
            safe_int(r.get("CONTRACTS")),    # volume
            turnover,           # turnover
            None,               # num_trades (not in old format)
            "EOD",              # session_id
            None,               # lot_size
            None,               # remarks
            None,               # reserved1
            None,               # reserved2
            None,               # reserved3
            None,               # reserved4
        ))
    return rows


def parse_fo_new(csv_text, target_date):
    """Parse new F&O bhavcopy CSV."""
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for r in reader:
        r = {k.strip(): v.strip() if v else v for k, v in r.items()}

        instrument_type = r.get("FinInstrmTp", "").strip()
        symbol = r.get("TckrSymb", "").strip()
        if not symbol:
            continue

        # Parse instrument_id from FinInstrmId
        fin_instrm_id = r.get("FinInstrmId", "").strip()
        if fin_instrm_id:
            try:
                instrument_id = int(fin_instrm_id)
            except ValueError:
                # Hash it if not a clean integer
                instrument_id = int(hashlib.md5(fin_instrm_id.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF
        else:
            # Generate from composite key
            instrument_id = generate_fo_instrument_id(
                target_date, instrument_type, symbol,
                r.get("XpryDt", ""), r.get("StrkPric", ""), r.get("OptnTp", "")
            )

        def parse_date_field(val):
            if not val or val in ("-", ""):
                return None
            try:
                return datetime.datetime.strptime(val, "%Y-%m-%d").date()
            except ValueError:
                try:
                    return parse_date_ddmmmyyyy(val)
                except ValueError:
                    return None

        trad_dt = parse_date_field(r.get("TradDt")) or target_date
        biz_dt = parse_date_field(r.get("BizDt")) or target_date
        expiry = parse_date_field(r.get("XpryDt"))
        actual_expiry = parse_date_field(r.get("ActlXpryDt"))

        rows.append((
            trad_dt,
            biz_dt,
            r.get("Sgmt", "NFO-FO").strip(),
            r.get("Src", "NSE").strip(),
            instrument_type,
            instrument_id,
            r.get("ISIN", "").strip() or None,
            symbol,
            r.get("SctySrs", "").strip() or None,
            expiry,
            actual_expiry,
            safe_float(r.get("StrkPric")),
            r.get("OptnTp", "").strip() or None,
            r.get("FinInstrmNm", "").strip() or None,
            safe_float(r.get("OpnPric")),
            safe_float(r.get("HghPric")),
            safe_float(r.get("LwPric")),
            safe_float(r.get("ClsPric")),
            safe_float(r.get("LastPric")),
            safe_float(r.get("PrvsClsgPric")),
            safe_float(r.get("UndrlygPric")),
            safe_float(r.get("SttlmPric")),
            safe_int(r.get("OpnIntrst")),
            safe_int(r.get("ChngInOpnIntrst")),
            safe_int(r.get("TtlTradgVol")),
            safe_float(r.get("TtlTrfVal")),
            safe_int(r.get("TtlNbOfTxsExctd")),
            r.get("SsnId", "").strip() or None,
            safe_int(r.get("NewBrdLotQty")),
            r.get("Rmks", "").strip() or None,
            r.get("Rsvd1", "").strip() or None,
            r.get("Rsvd2", "").strip() or None,
            r.get("Rsvd3", "").strip() or None,
            r.get("Rsvd4", "").strip() or None,
        ))
    return rows


FO_INSERT = """
    INSERT INTO nse_fo_bhavcopy (
        date, biz_date, segment, source, instrument_type, instrument_id,
        isin, symbol, series, expiry, actual_expiry, strike, option_type,
        instrument_name, open, high, low, close, last, prev_close,
        underlying, settlement, oi, oi_change, volume, turnover, num_trades,
        session_id, lot_size, remarks, reserved1, reserved2, reserved3, reserved4
    ) VALUES %s
    ON CONFLICT (date, instrument_id) DO NOTHING
"""


def process_fo(d, conn):
    """Fetch and insert F&O bhavcopy for date d. Returns (parsed, inserted)."""
    if d < FORMAT_SWITCH_DATE:
        url = url_fo_old(d)
    else:
        url = url_fo_new(d)

    data, status = fetch_url(url)
    if status == 404:
        return -1, 0
    if data is None:
        return -2, 0

    csv_text = extract_csv_from_zip(data)
    if csv_text is None:
        log.error(f"FO {d}: could not extract CSV from zip")
        return -2, 0

    if d < FORMAT_SWITCH_DATE:
        rows = parse_fo_old(csv_text, d)
    else:
        rows = parse_fo_new(csv_text, d)

    parsed = len(rows)
    if parsed == 0:
        return 0, 0

    # Insert in batches to avoid memory issues (FO can have 5000+ rows)
    inserted_total = 0
    batch_size = 1000
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            psycopg2.extras.execute_values(cur, FO_INSERT, batch, page_size=500)
            inserted_total += cur.rowcount
    conn.commit()
    return parsed, inserted_total


# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------
class Stats:
    def __init__(self):
        self.dates_attempted = 0
        self.dates_succeeded = 0
        self.dates_failed = 0
        self.dates_holiday = 0
        self.feed_stats = defaultdict(lambda: {
            "parsed": 0, "inserted": 0, "holidays": 0, "errors": 0,
            "monthly": defaultdict(lambda: {"parsed": 0, "inserted": 0}),
        })

    def record(self, feed, d, parsed, inserted):
        fs = self.feed_stats[feed]
        month_key = d.strftime("%Y-%m")
        if parsed == -1:
            fs["holidays"] += 1
        elif parsed == -2:
            fs["errors"] += 1
        else:
            fs["parsed"] += parsed
            fs["inserted"] += inserted
            fs["monthly"][month_key]["parsed"] += parsed
            fs["monthly"][month_key]["inserted"] += inserted


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------
def write_validation_report(conn, stats):
    lines = []
    lines.append("=" * 70)
    lines.append("BACKFILL VALIDATION REPORT")
    lines.append(f"Generated: {datetime.datetime.now().isoformat()}")
    lines.append("=" * 70)

    # Overall stats
    lines.append(f"\nDates attempted:  {stats.dates_attempted}")
    lines.append(f"Dates succeeded:  {stats.dates_succeeded}")
    lines.append(f"Dates failed:     {stats.dates_failed}")
    lines.append(f"Dates holiday:    {stats.dates_holiday}")

    # Per-feed stats
    for feed_name in ["cm", "idx", "fo"]:
        fs = stats.feed_stats[feed_name]
        lines.append(f"\n--- {feed_name.upper()} ---")
        lines.append(f"  Total parsed:   {fs['parsed']}")
        lines.append(f"  Total inserted: {fs['inserted']}")
        lines.append(f"  Holidays:       {fs['holidays']}")
        lines.append(f"  Errors:         {fs['errors']}")
        lines.append(f"  Monthly breakdown:")
        for month in sorted(fs["monthly"].keys()):
            ms = fs["monthly"][month]
            lines.append(f"    {month}: parsed={ms['parsed']}, inserted={ms['inserted']}")

    # DB row counts
    with conn.cursor() as cur:
        for table in ["nse_cm_bhavcopy", "nse_indices_daily", "nse_fo_bhavcopy"]:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            total = cur.fetchone()[0]
            lines.append(f"\n{table}: {total:,} total rows")

            cur.execute(f"SELECT EXTRACT(YEAR FROM date)::int as yr, COUNT(*) FROM {table} GROUP BY yr ORDER BY yr")
            for yr, cnt in cur.fetchall():
                lines.append(f"  {yr}: {cnt:,}")

            cur.execute(f"SELECT MIN(date), MAX(date) FROM {table}")
            min_d, max_d = cur.fetchone()
            lines.append(f"  Date range: {min_d} to {max_d}")

    # Sanity checks
    lines.append("\n--- SANITY CHECKS ---")
    sanity_dates = [
        datetime.date(2020, 3, 23),  # COVID crash
        datetime.date(2020, 1, 2),   # First trading day 2020
        datetime.date(2024, 1, 2),   # First new-format day (approx)
    ]
    with conn.cursor() as cur:
        for sd in sanity_dates:
            for table in ["nse_cm_bhavcopy", "nse_indices_daily", "nse_fo_bhavcopy"]:
                cur.execute(f"SELECT COUNT(*) FROM {table} WHERE date = %s", (sd,))
                cnt = cur.fetchone()[0]
                status = "✓" if cnt > 0 else "✗ MISSING"
                lines.append(f"  {sd} in {table}: {cnt} rows {status}")

    # Gap analysis for CM
    lines.append("\n--- DATE GAP ANALYSIS (CM Bhavcopy) ---")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT date FROM nse_cm_bhavcopy
            WHERE date BETWEEN '2020-01-01' AND '2025-08-31'
            GROUP BY date ORDER BY date
        """)
        cm_dates = [r[0] for r in cur.fetchall()]
        if cm_dates:
            lines.append(f"  First date: {cm_dates[0]}")
            lines.append(f"  Last date:  {cm_dates[-1]}")
            lines.append(f"  Total trading days: {len(cm_dates)}")

            # Find gaps > 3 days (potential missing data, not just weekends/holidays)
            gaps = []
            for i in range(1, len(cm_dates)):
                diff = (cm_dates[i] - cm_dates[i-1]).days
                if diff > 4:  # more than a long weekend
                    gaps.append((cm_dates[i-1], cm_dates[i], diff))
            if gaps:
                lines.append(f"  Gaps > 4 days ({len(gaps)}):")
                for g_start, g_end, g_days in gaps[:20]:
                    lines.append(f"    {g_start} → {g_end} ({g_days} days)")
            else:
                lines.append("  No unusual gaps found")

    report = "\n".join(lines)
    with open(REPORT_FILE, "w") as f:
        f.write(report)
    log.info(f"Validation report written to {REPORT_FILE}")
    print("\n" + report)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="NSE Historical Data Backfill")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--feed", type=str, default="all", choices=["cm", "idx", "fo", "all"],
                        help="Which feed to backfill")
    args = parser.parse_args()

    start = datetime.datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else DEFAULT_START
    end = datetime.datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else DEFAULT_END

    feeds_to_run = ["cm", "idx", "fo"] if args.feed == "all" else [args.feed]

    log.info(f"=" * 70)
    log.info(f"NSE HISTORICAL BACKFILL")
    log.info(f"Date range: {start} to {end}")
    log.info(f"Feeds: {', '.join(feeds_to_run)}")
    log.info(f"Format switch date: {FORMAT_SWITCH_DATE}")
    log.info(f"=" * 70)

    # Connect to DB
    conn = psycopg2.connect(DB_DSN)
    log.info("Connected to database")

    # Load checkpoint
    ckpt = load_checkpoint()
    stats = Stats()

    all_dates = list(trading_dates(start, end))
    total_dates = len(all_dates)
    log.info(f"Total weekday dates in range: {total_dates}")

    start_time = time.time()

    for i, d in enumerate(all_dates):
        d_str = d.isoformat()
        stats.dates_attempted += 1

        # Progress
        if (i + 1) % PROGRESS_INTERVAL == 0 or i == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (total_dates - i - 1) / rate / 3600 if rate > 0 else 0
            log.info(f"Progress: {i+1}/{total_dates} dates ({d_str}) | "
                     f"Rate: {rate:.1f} dates/s | ETA: {eta:.1f}h")

        date_had_error = False
        date_all_holiday = True

        for feed in feeds_to_run:
            # Check checkpoint
            if ckpt.get(feed, {}).get(d_str):
                continue

            t0 = time.time()
            try:
                if feed == "cm":
                    parsed, inserted = process_cm(d, conn)
                elif feed == "idx":
                    parsed, inserted = process_idx(d, conn)
                elif feed == "fo":
                    parsed, inserted = process_fo(d, conn)
                else:
                    continue

                elapsed_feed = time.time() - t0
                stats.record(feed, d, parsed, inserted)

                if parsed == -1:
                    log.debug(f"{feed.upper()} {d_str}: holiday (404)")
                elif parsed == -2:
                    log.error(f"{feed.upper()} {d_str}: FAILED after retries ({elapsed_feed:.1f}s)")
                    date_had_error = True
                    date_all_holiday = False
                else:
                    skipped = parsed - inserted
                    log.info(f"{feed.upper()} {d_str}: parsed={parsed}, inserted={inserted}, "
                             f"skipped={skipped}, time={elapsed_feed:.1f}s")
                    date_all_holiday = False

                    # Mark checkpoint on success
                    if feed not in ckpt:
                        ckpt[feed] = {}
                    ckpt[feed][d_str] = True

            except Exception as e:
                log.error(f"{feed.upper()} {d_str}: EXCEPTION: {e}")
                log.error(traceback.format_exc())
                date_had_error = True
                date_all_holiday = False
                # Rollback on error
                try:
                    conn.rollback()
                except Exception:
                    pass

            # Rate limiting between requests
            time.sleep(REQUEST_DELAY)

        # Save checkpoint periodically
        if (i + 1) % 10 == 0:
            save_checkpoint(ckpt)

        if date_had_error:
            stats.dates_failed += 1
        elif date_all_holiday:
            stats.dates_holiday += 1
        else:
            stats.dates_succeeded += 1

    # Final checkpoint save
    save_checkpoint(ckpt)

    total_time = time.time() - start_time
    log.info(f"\n{'=' * 70}")
    log.info(f"BACKFILL COMPLETE")
    log.info(f"Total time: {total_time/3600:.1f} hours ({total_time:.0f}s)")
    log.info(f"Dates: {stats.dates_attempted} attempted, {stats.dates_succeeded} succeeded, "
             f"{stats.dates_failed} failed, {stats.dates_holiday} holidays")
    for feed_name in feeds_to_run:
        fs = stats.feed_stats[feed_name]
        log.info(f"  {feed_name.upper()}: parsed={fs['parsed']}, inserted={fs['inserted']}, "
                 f"holidays={fs['holidays']}, errors={fs['errors']}")
    log.info(f"{'=' * 70}")

    # Validation report
    write_validation_report(conn, stats)

    conn.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
