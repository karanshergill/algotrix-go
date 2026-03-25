#!/usr/bin/env python3
"""NSE News Collector — polls NSE India APIs and stores news in PostgreSQL."""

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
import requests
import schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("nse-collector")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "atdb")
DB_USER = os.getenv("DB_USER", "me")
DB_PASS = os.getenv("DB_PASS", "algotrix")

NSE_BASE = "https://www.nseindia.com"
NSE_API = NSE_BASE + "/api"

MARKET_MOVING_EXACT = {
    "Outcome of Board Meeting",
    "Disclosure under SEBI Takeover Regulations",
    "Spurt in Volume",
    "Credit Rating- Others",
}
MARKET_MOVING_CONTAINS = ["Acquisition", "Buyback", "Takeover", "Delisting"]

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    log.info("Received signal %s, shutting down…", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ---------------------------------------------------------------------------
# NSE Session
# ---------------------------------------------------------------------------
_session: requests.Session | None = None


def get_session() -> requests.Session:
    """Return a requests session with valid NSE cookies."""
    global _session
    if _session is None:
        _session = _new_session()
    return _session


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": NSE_BASE,
    })
    log.info("Establishing NSE session…")
    # NSE may return 403 on homepage but still sets cookies — that's fine
    try:
        s.get(NSE_BASE, timeout=15)
    except requests.RequestException:
        pass
    # If no cookies, try the announcements page which sometimes works better
    if not s.cookies:
        try:
            s.get(f"{NSE_BASE}/companies-listing/corporate-filings-announcements", timeout=15)
        except requests.RequestException:
            pass
    log.info("NSE session established (cookies: %d)", len(s.cookies))
    return s


def reset_session():
    global _session
    _session = None


def nse_get(path: str, params: dict | None = None) -> dict | list | None:
    """GET an NSE API endpoint, handling 403 with session refresh."""
    url = f"{NSE_API}/{path}"
    for attempt in range(3):
        try:
            s = get_session()
            r = s.get(url, params=params, timeout=20)
            if r.status_code == 403:
                log.warning("Got 403 on %s, refreshing session (attempt %d)", path, attempt + 1)
                reset_session()
                time.sleep(2)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log.error("Request error on %s (attempt %d): %s", path, attempt + 1, e)
            reset_session()
            time.sleep(3)
    log.error("Failed to fetch %s after 3 attempts", path)
    return None

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
_conn = None

DDL = """
CREATE TABLE IF NOT EXISTS nse_announcements (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    category TEXT,
    description TEXT,
    announcement_dt TIMESTAMP,
    attachment_url TEXT,
    is_market_moving BOOLEAN DEFAULT FALSE,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, announcement_dt, category)
);

CREATE TABLE IF NOT EXISTS nse_block_deals (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    series TEXT,
    session TEXT,
    traded_volume BIGINT,
    traded_value NUMERIC,
    price NUMERIC,
    deal_date DATE,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, deal_date, session, traded_volume)
);

CREATE TABLE IF NOT EXISTS nse_insider_trading (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    acquirer_name TEXT,
    acquisition_mode TEXT,
    shares_acquired NUMERIC,
    value NUMERIC,
    transaction_date DATE,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, acquirer_name, transaction_date, shares_acquired)
);

CREATE TABLE IF NOT EXISTS nse_board_meetings (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    meeting_date DATE,
    purpose TEXT,
    description TEXT,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, meeting_date, purpose)
);

CREATE TABLE IF NOT EXISTS nse_corporate_actions (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    subject TEXT,
    ex_date DATE,
    record_date DATE,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, ex_date, subject)
);
"""


def get_conn():
    global _conn
    if _conn is None or _conn.closed:
        dsn = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER}"
        if DB_PASS:
            dsn += f" password={DB_PASS}"
        _conn = psycopg2.connect(dsn)
        _conn.autocommit = True
    return _conn


def init_db():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(DDL)
    log.info("Database tables ensured")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_numeric(val) -> float | None:
    """Convert a value to float, returning None for '-', empty, or non-numeric."""
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_date(s: str | None, fmt: str = "%d-%b-%Y") -> "datetime.date | None":
    if not s:
        return None
    # Strip time component if present (e.g. "23-Mar-2026 14:51" → "23-Mar-2026")
    cleaned = s.strip().split(" ")[0] if " " in str(s).strip() else str(s).strip()
    for f in (fmt, "%d-%m-%Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, f).date()
        except (ValueError, AttributeError):
            continue
    # Try full string with time formats as fallback
    for f in ("%d-%b-%Y %H:%M", "%d-%b-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), f).date()
        except (ValueError, AttributeError):
            continue
    return None


def parse_ts(s: str | None) -> "datetime | None":
    if not s:
        return None
    for f in ("%d-%b-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d %b %Y %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), f)
        except (ValueError, AttributeError):
            continue
    return None


def is_market_moving(category: str | None) -> bool:
    if not category:
        return False
    if category in MARKET_MOVING_EXACT:
        return True
    return any(kw in category for kw in MARKET_MOVING_CONTAINS)


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------
def collect_announcements():
    log.info("Collecting corporate announcements…")
    data = nse_get("corporate-announcements", {"index": "equities"})
    if not data:
        return 0
    rows = data if isinstance(data, list) else data.get("data", data.get("rows", []))
    if not isinstance(rows, list):
        log.warning("Unexpected announcements response type: %s", type(rows))
        return 0

    conn = get_conn()
    count = 0
    with conn.cursor() as cur:
        for item in rows:
            symbol = item.get("symbol", item.get("sm_name", ""))
            category = item.get("desc", "")
            description = item.get("attchmntText", item.get("smIndustry", ""))
            an_dt = parse_ts(item.get("an_dt"))
            attachment = item.get("attchmntFile", "")
            if attachment and not attachment.startswith("http"):
                attachment = NSE_BASE + "/" + attachment.lstrip("/")
            cur.execute(
                """INSERT INTO nse_announcements
                   (symbol, category, description, announcement_dt, attachment_url,
                    is_market_moving, raw_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (symbol, category, description, an_dt, attachment,
                 is_market_moving(category),
                 psycopg2.extras.Json(item)),
            )
            count += cur.rowcount
    log.info("Announcements: %d new rows", count)
    return count


def collect_block_deals():
    log.info("Collecting block deals…")
    data = nse_get("block-deal")
    if not data:
        return 0
    rows = data.get("data", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        log.warning("Unexpected block deals response type: %s", type(rows))
        return 0

    conn = get_conn()
    count = 0
    with conn.cursor() as cur:
        for item in rows:
            symbol = item.get("symbol", "")
            ts = item.get("lastUpdateTime") or item.get("timestamp") or ""
            deal_date = parse_ts(ts)
            if deal_date:
                deal_date = deal_date.date()
            else:
                deal_date = parse_date(ts)
            price = item.get("lastPrice") or item.get("price") or item.get("open")
            cur.execute(
                """INSERT INTO nse_block_deals
                   (symbol, series, session, traded_volume, traded_value, price,
                    deal_date, raw_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (symbol,
                 item.get("series", ""),
                 item.get("session", ""),
                 item.get("totalTradedVolume"),
                 item.get("totalTradedValue"),
                 price,
                 deal_date,
                 psycopg2.extras.Json(item)),
            )
            count += cur.rowcount
    log.info("Block deals: %d new rows", count)
    return count


def collect_insider_trading():
    log.info("Collecting insider trading…")
    today = datetime.now()
    from_date = (today - timedelta(days=7)).strftime("%d-%m-%Y")
    to_date = today.strftime("%d-%m-%Y")
    data = nse_get("corporates-pit", {
        "index": "equities",
        "from_date": from_date,
        "to_date": to_date,
    })
    if not data:
        return 0
    rows = data.get("data", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        log.warning("Unexpected insider trading response type: %s", type(rows))
        return 0

    conn = get_conn()
    count = 0
    with conn.cursor() as cur:
        for item in rows:
            symbol = item.get("symbol", "")
            acquirer = item.get("acqName", "")
            # Prefer acqfromDt (actual transaction date), then intimDt (clean date), then date (has time)
            txn_date = parse_date(item.get("acqfromDt")) or parse_date(item.get("intimDt")) or parse_date(item.get("date"))
            cur.execute(
                """INSERT INTO nse_insider_trading
                   (symbol, acquirer_name, acquisition_mode, shares_acquired,
                    value, transaction_date, raw_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (symbol,
                 acquirer,
                 item.get("acqMode", ""),
                 _safe_numeric(item.get("secAcq")),
                 _safe_numeric(item.get("secVal")),
                 txn_date,
                 psycopg2.extras.Json(item)),
            )
            count += cur.rowcount
    log.info("Insider trading: %d new rows", count)
    return count


def collect_board_meetings():
    log.info("Collecting board meetings…")
    data = nse_get("corporate-board-meetings", {"index": "equities"})
    if not data:
        return 0
    rows = data if isinstance(data, list) else data.get("data", data.get("rows", []))
    if not isinstance(rows, list):
        log.warning("Unexpected board meetings response type: %s", type(rows))
        return 0

    conn = get_conn()
    count = 0
    with conn.cursor() as cur:
        for item in rows:
            symbol = item.get("bm_symbol", item.get("symbol", ""))
            meeting_date = parse_date(item.get("bm_date"))
            purpose = item.get("bm_purpose", "")
            description = item.get("bm_desc", "")
            cur.execute(
                """INSERT INTO nse_board_meetings
                   (symbol, meeting_date, purpose, description, raw_json)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (symbol, meeting_date, purpose, description,
                 psycopg2.extras.Json(item)),
            )
            count += cur.rowcount
    log.info("Board meetings: %d new rows", count)
    return count


def collect_corporate_actions():
    log.info("Collecting corporate actions…")
    today = datetime.now()
    from_date = (today - timedelta(days=30)).strftime("%d-%m-%Y")
    to_date = (today + timedelta(days=30)).strftime("%d-%m-%Y")
    data = nse_get("corporates-corporateActions", {
        "index": "equities",
        "from_date": from_date,
        "to_date": to_date,
    })
    if not data:
        return 0
    rows = data if isinstance(data, list) else data.get("data", data.get("rows", []))
    if not isinstance(rows, list):
        log.warning("Unexpected corporate actions response type: %s", type(rows))
        return 0

    conn = get_conn()
    count = 0
    with conn.cursor() as cur:
        for item in rows:
            symbol = item.get("symbol", "")
            subject = item.get("subject", "")
            ex_date = parse_date(item.get("exDate"))
            rec_date = parse_date(item.get("recDate"))
            cur.execute(
                """INSERT INTO nse_corporate_actions
                   (symbol, subject, ex_date, record_date, raw_json)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (symbol, subject, ex_date, rec_date,
                 psycopg2.extras.Json(item)),
            )
            count += cur.rowcount
    log.info("Corporate actions: %d new rows", count)
    return count


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------
ALL_COLLECTORS = [
    collect_announcements,
    collect_block_deals,
    collect_insider_trading,
    collect_board_meetings,
    collect_corporate_actions,
]


def run_cycle():
    log.info("=== Starting collection cycle ===")
    for fn in ALL_COLLECTORS:
        if _shutdown:
            break
        try:
            fn()
        except Exception:
            log.exception("Error in %s", fn.__name__)
        if not _shutdown:
            time.sleep(1)  # be polite to NSE
    log.info("=== Cycle complete ===")


def current_interval_minutes() -> int:
    """Return polling interval based on IST time of day."""
    # IST = UTC + 5:30
    from datetime import timezone
    ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    hour_min = ist_now.hour * 60 + ist_now.minute

    market_open = 9 * 60        # 09:00
    market_close = 15 * 60 + 45  # 15:45
    night_start = 20 * 60        # 20:00

    if market_open <= hour_min < market_close:
        return 2
    elif market_close <= hour_min < night_start:
        return 10
    else:
        return 30


def main():
    parser = argparse.ArgumentParser(description="NSE News Collector")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    log.info("NSE News Collector starting…")
    init_db()

    if args.once:
        run_cycle()
        log.info("Single cycle done, exiting.")
        return

    # Schedule-based loop
    run_cycle()  # run immediately on start

    while not _shutdown:
        interval = current_interval_minutes()
        next_run = time.time() + interval * 60
        log.info("Next cycle in %d minutes", interval)
        while time.time() < next_run and not _shutdown:
            time.sleep(1)
        if not _shutdown:
            run_cycle()

    log.info("Shutdown complete.")


if __name__ == "__main__":
    main()
