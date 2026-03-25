#!/usr/bin/env python3
"""Pre-Open Market Data Collector for AlgoTrix-Go.

Connects to Fyers WebSocket at 8:58 AM IST, captures pre-open session data
(9:00-9:08 AM), records opening prices at 9:15 AM, and closing prices at 15:30.

Tables created/used:
  - preopen_snapshots: raw tick snapshots every 15s during pre-open
  - preopen_features_live: computed market-wide features per snapshot

Usage:
  python3 -u regime-classifier/src/preopen_collector.py
"""

import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime, date, timedelta

import psycopg2
import psycopg2.extras
import pytz

from fyers_apiv3.FyersWebsocket import data_ws

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
APP_ID = "EQHA0N51WU-100"
TOKEN_PATH = "engine/token.json"
DB_DSN = "postgres://me:algotrix@localhost:5432/atdb"
IST = pytz.timezone("Asia/Kolkata")

INDEX_SYMBOLS = ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"]

# Schedule (IST)
CONNECT_TIME = (8, 58)   # Connect at 8:58
PREOPEN_START = (9, 0)    # Snapshots from 9:00
PREOPEN_END = (9, 8)      # Snapshots until 9:08
SNAPSHOT_INTERVAL = 15    # seconds
OPEN_RECORD_TIME = (9, 15)
CLOSE_RECORD_TIME = (15, 30)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("preopen")

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
latest_ticks = {}   # symbol -> dict of latest tick data
tick_lock = threading.Lock()
shutdown_event = threading.Event()


def now_ist() -> datetime:
    return datetime.now(IST)


def ist_time(h, m, s=0) -> datetime:
    today = date.today()
    return IST.localize(datetime(today.year, today.month, today.day, h, m, s))


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db():
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    return conn


def ensure_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS preopen_snapshots (
            date         DATE NOT NULL,
            snapshot_time TIMESTAMPTZ NOT NULL,
            fy_symbol    TEXT NOT NULL,
            ltp          DOUBLE PRECISION,
            prev_close   DOUBLE PRECISION,
            gap_pct      DOUBLE PRECISION,
            total_buy_qty BIGINT,
            total_sell_qty BIGINT,
            PRIMARY KEY (date, snapshot_time, fy_symbol)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS preopen_features_live (
            date              DATE NOT NULL,
            snapshot_time     TIMESTAMPTZ NOT NULL,
            nifty_gap_pct     DOUBLE PRECISION,
            market_imbalance_ratio DOUBLE PRECISION,
            gap_up_count      INTEGER,
            gap_down_count    INTEGER,
            flat_count        INTEGER,
            breadth_ratio     DOUBLE PRECISION,
            imbalance_velocity DOUBLE PRECISION,
            nifty_ltp         DOUBLE PRECISION,
            banknifty_ltp     DOUBLE PRECISION,
            PRIMARY KEY (date, snapshot_time)
        );
    """)
    cur.close()
    log.info("Tables ensured: preopen_snapshots, preopen_features_live")


def load_symbols(conn) -> list[str]:
    """Load all active equity fy_symbols from the symbols table."""
    cur = conn.cursor()
    cur.execute("""
        SELECT fy_symbol FROM symbols
        WHERE status = 'active' AND series = 'EQ'
          AND fy_symbol IS NOT NULL AND fy_symbol != ''
    """)
    rows = cur.fetchall()
    cur.close()
    symbols = [r[0] for r in rows]
    log.info("Loaded %d active EQ symbols from DB", len(symbols))
    return symbols


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------
def load_access_token() -> str:
    with open(TOKEN_PATH) as f:
        data = json.load(f)
    raw = data["access_token"]
    # Fyers Python SDK expects "APP_ID:access_token" format
    return f"{APP_ID}:{raw}"


# ---------------------------------------------------------------------------
# WebSocket callbacks
# ---------------------------------------------------------------------------
def on_message(msg):
    """Handle incoming tick data from Fyers WebSocket."""
    if not isinstance(msg, dict):
        return
    symbol = msg.get("symbol")
    if not symbol:
        return
    with tick_lock:
        latest_ticks[symbol] = msg


def on_connect():
    log.info("WebSocket connected")


def on_close(msg):
    log.warning("WebSocket closed: %s", msg)


def on_error(msg):
    log.error("WebSocket error: %s", msg)


# ---------------------------------------------------------------------------
# Snapshot logic
# ---------------------------------------------------------------------------
def take_snapshot(conn, snap_time: datetime, prev_imbalance: float | None) -> float | None:
    """Capture current tick state, save raw + computed features. Returns current imbalance ratio."""
    today = date.today()

    with tick_lock:
        ticks = dict(latest_ticks)

    if not ticks:
        log.warning("No ticks available for snapshot at %s", snap_time.strftime("%H:%M:%S"))
        return prev_imbalance

    # Raw snapshots
    rows = []
    total_buy = 0
    total_sell = 0
    gap_up = 0
    gap_down = 0
    flat = 0
    nifty_ltp = None
    nifty_prev_close = None
    banknifty_ltp = None

    for symbol, tick in ticks.items():
        ltp = tick.get("ltp")
        prev_close = tick.get("prev_close_price")
        tbq = tick.get("tot_buy_qty", 0) or 0
        tsq = tick.get("tot_sell_qty", 0) or 0

        # Index handling
        if symbol == "NSE:NIFTY50-INDEX":
            nifty_ltp = ltp
            nifty_prev_close = prev_close
            continue
        elif symbol == "NSE:NIFTYBANK-INDEX":
            banknifty_ltp = ltp
            continue

        # Gap calculation
        gap_pct = None
        if ltp and prev_close and prev_close > 0:
            gap_pct = ((ltp / prev_close) - 1) * 100

        rows.append((today, snap_time, symbol, ltp, prev_close, gap_pct, int(tbq), int(tsq)))

        # Aggregations
        total_buy += int(tbq)
        total_sell += int(tsq)
        if gap_pct is not None:
            if gap_pct > 0.1:
                gap_up += 1
            elif gap_pct < -0.1:
                gap_down += 1
            else:
                flat += 1

    # Save raw snapshots
    if rows:
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO preopen_snapshots (date, snapshot_time, fy_symbol, ltp, prev_close, gap_pct, total_buy_qty, total_sell_qty)
               VALUES %s
               ON CONFLICT (date, snapshot_time, fy_symbol) DO UPDATE SET
                   ltp = EXCLUDED.ltp, prev_close = EXCLUDED.prev_close,
                   gap_pct = EXCLUDED.gap_pct, total_buy_qty = EXCLUDED.total_buy_qty,
                   total_sell_qty = EXCLUDED.total_sell_qty""",
            rows,
            page_size=500,
        )
        cur.close()

    # Computed features
    nifty_gap_pct = None
    if nifty_ltp and nifty_prev_close and nifty_prev_close > 0:
        nifty_gap_pct = ((nifty_ltp / nifty_prev_close) - 1) * 100

    imbalance_ratio = total_buy / total_sell if total_sell > 0 else None
    total_gapped = gap_up + gap_down + flat
    breadth_ratio = gap_up / total_gapped if total_gapped > 0 else None
    imbalance_velocity = None
    if prev_imbalance is not None and imbalance_ratio is not None:
        imbalance_velocity = imbalance_ratio - prev_imbalance

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO preopen_features_live
           (date, snapshot_time, nifty_gap_pct, market_imbalance_ratio,
            gap_up_count, gap_down_count, flat_count, breadth_ratio,
            imbalance_velocity, nifty_ltp, banknifty_ltp)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (date, snapshot_time) DO UPDATE SET
               nifty_gap_pct = EXCLUDED.nifty_gap_pct,
               market_imbalance_ratio = EXCLUDED.market_imbalance_ratio,
               gap_up_count = EXCLUDED.gap_up_count,
               gap_down_count = EXCLUDED.gap_down_count,
               flat_count = EXCLUDED.flat_count,
               breadth_ratio = EXCLUDED.breadth_ratio,
               imbalance_velocity = EXCLUDED.imbalance_velocity,
               nifty_ltp = EXCLUDED.nifty_ltp,
               banknifty_ltp = EXCLUDED.banknifty_ltp""",
        (today, snap_time, nifty_gap_pct, imbalance_ratio,
         gap_up, gap_down, flat, breadth_ratio,
         imbalance_velocity, nifty_ltp, banknifty_ltp),
    )
    cur.close()

    log.info(
        "Snapshot %s | Nifty gap: %.2f%% | Imbalance: %.3f | Up/Down/Flat: %d/%d/%d | Breadth: %.3f | Velocity: %s",
        snap_time.strftime("%H:%M:%S"),
        nifty_gap_pct or 0,
        imbalance_ratio or 0,
        gap_up, gap_down, flat,
        breadth_ratio or 0,
        f"{imbalance_velocity:.4f}" if imbalance_velocity is not None else "N/A",
    )

    return imbalance_ratio


def record_prices(conn, label: str):
    """Record current LTP as open/close prices into preopen_snapshots."""
    today = date.today()
    snap_time = now_ist()

    with tick_lock:
        ticks = dict(latest_ticks)

    if not ticks:
        log.warning("No ticks for %s price recording", label)
        return

    rows = []
    for symbol, tick in ticks.items():
        if symbol in INDEX_SYMBOLS:
            continue
        ltp = tick.get("ltp")
        prev_close = tick.get("prev_close_price")
        tbq = tick.get("tot_buy_qty", 0) or 0
        tsq = tick.get("tot_sell_qty", 0) or 0
        gap_pct = None
        if ltp and prev_close and prev_close > 0:
            gap_pct = ((ltp / prev_close) - 1) * 100
        rows.append((today, snap_time, symbol, ltp, prev_close, gap_pct, int(tbq), int(tsq)))

    if rows:
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO preopen_snapshots (date, snapshot_time, fy_symbol, ltp, prev_close, gap_pct, total_buy_qty, total_sell_qty)
               VALUES %s
               ON CONFLICT (date, snapshot_time, fy_symbol) DO UPDATE SET
                   ltp = EXCLUDED.ltp, prev_close = EXCLUDED.prev_close,
                   gap_pct = EXCLUDED.gap_pct, total_buy_qty = EXCLUDED.total_buy_qty,
                   total_sell_qty = EXCLUDED.total_sell_qty""",
            rows,
            page_size=500,
        )
        cur.close()

    # Also record index LTPs in features table
    nifty_ltp = ticks.get("NSE:NIFTY50-INDEX", {}).get("ltp")
    banknifty_ltp = ticks.get("NSE:NIFTYBANK-INDEX", {}).get("ltp")
    nifty_prev = ticks.get("NSE:NIFTY50-INDEX", {}).get("prev_close_price")
    nifty_gap = ((nifty_ltp / nifty_prev) - 1) * 100 if nifty_ltp and nifty_prev and nifty_prev > 0 else None

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO preopen_features_live
           (date, snapshot_time, nifty_gap_pct, nifty_ltp, banknifty_ltp,
            market_imbalance_ratio, gap_up_count, gap_down_count, flat_count,
            breadth_ratio, imbalance_velocity)
           VALUES (%s, %s, %s, %s, %s, NULL, NULL, NULL, NULL, NULL, NULL)
           ON CONFLICT (date, snapshot_time) DO UPDATE SET
               nifty_gap_pct = EXCLUDED.nifty_gap_pct,
               nifty_ltp = EXCLUDED.nifty_ltp,
               banknifty_ltp = EXCLUDED.banknifty_ltp""",
        (today, snap_time, nifty_gap, nifty_ltp, banknifty_ltp),
    )
    cur.close()

    log.info("%s prices recorded: %d symbols | Nifty: %s | BankNifty: %s",
             label, len(rows), nifty_ltp, banknifty_ltp)


def compute_day_regime(conn):
    """Compute E3 regime label using the day's actual data and save it.

    We compute a simplified version: just store the return and direction
    based on open vs close. Full E3 requires rolling stats which the
    batch pipeline handles — here we record the raw inputs.
    """
    today = date.today()
    cur = conn.cursor()

    # Get opening snapshot (9:15) and closing snapshot (15:30)
    cur.execute("""
        SELECT snapshot_time, nifty_ltp
        FROM preopen_features_live
        WHERE date = %s
        ORDER BY snapshot_time
    """, (today,))
    rows = cur.fetchall()
    cur.close()

    if len(rows) < 2:
        log.warning("Not enough snapshots to compute regime")
        return

    nifty_open = None
    nifty_close = None
    for ts, ltp in rows:
        if ltp is not None:
            if nifty_open is None:
                nifty_open = ltp
            nifty_close = ltp

    if nifty_open and nifty_close and nifty_open > 0:
        day_return = ((nifty_close / nifty_open) - 1) * 100
        if day_return > 0.3:
            regime = "Trend-Up"
        elif day_return < -0.3:
            regime = "Trend-Down"
        else:
            regime = "Range"
        log.info("Day regime: %s (return: %.2f%%, open: %.2f, close: %.2f)",
                 regime, day_return, nifty_open, nifty_close)
    else:
        log.warning("Could not compute regime: open=%s close=%s", nifty_open, nifty_close)


# ---------------------------------------------------------------------------
# Sleep helpers
# ---------------------------------------------------------------------------
def sleep_until(target: datetime):
    """Sleep until target time, checking shutdown_event every second."""
    while not shutdown_event.is_set():
        remaining = (target - now_ist()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))


def wait_seconds(secs: float):
    """Wait for N seconds, checking shutdown_event."""
    deadline = time.monotonic() + secs
    while not shutdown_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Signal handling
    def handle_signal(signum, frame):
        log.info("Received signal %d, shutting down...", signum)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("Pre-open collector starting for %s", date.today())

    # DB setup
    conn = get_db()
    ensure_tables(conn)
    symbols = load_symbols(conn)
    all_symbols = symbols + INDEX_SYMBOLS
    log.info("Total symbols to subscribe: %d (+ %d indices)", len(symbols), len(INDEX_SYMBOLS))

    # Load token
    access_token = load_access_token()
    log.info("Access token loaded (app: %s)", APP_ID)

    # Wait until connect time
    connect_at = ist_time(*CONNECT_TIME)
    if now_ist() < connect_at:
        log.info("Waiting until %s IST to connect...", connect_at.strftime("%H:%M"))
        sleep_until(connect_at)
        if shutdown_event.is_set():
            return

    # Connect WebSocket
    log.info("Connecting to Fyers WebSocket...")
    fyers_socket = data_ws.FyersDataSocket(
        access_token=access_token,
        log_path="./logs",
        litemode=False,
        write_to_file=False,
        reconnect=True,
        reconnect_retry=10,
        on_message=on_message,
        on_error=on_error,
        on_connect=on_connect,
        on_close=on_close,
    )

    fyers_socket.connect()

    # Subscribe in chunks (SDK handles chunking internally at 1500)
    # Use SymbolUpdate for full data (LTP + buy/sell qty + OHLC + prev_close)
    fyers_socket.subscribe(symbols=all_symbols, data_type="SymbolUpdate")
    log.info("Subscribed to %d symbols", len(all_symbols))

    # Wait for pre-open start
    preopen_start = ist_time(*PREOPEN_START)
    if now_ist() < preopen_start:
        log.info("Waiting until %s IST for pre-open session...", preopen_start.strftime("%H:%M"))
        sleep_until(preopen_start)
        if shutdown_event.is_set():
            fyers_socket.close_connection()
            conn.close()
            return

    # Take snapshots every 15 seconds from 9:00 to 9:08
    preopen_end = ist_time(*PREOPEN_END)
    prev_imbalance = None
    snapshot_count = 0

    log.info("=== Pre-open snapshot collection started ===")
    while now_ist() < preopen_end and not shutdown_event.is_set():
        snap_time = now_ist()
        prev_imbalance = take_snapshot(conn, snap_time, prev_imbalance)
        snapshot_count += 1
        wait_seconds(SNAPSHOT_INTERVAL)

    log.info("=== Pre-open collection done: %d snapshots ===", snapshot_count)

    # Wait for market open at 9:15 to record opening prices
    open_time = ist_time(*OPEN_RECORD_TIME)
    if now_ist() < open_time:
        log.info("Waiting until %s IST for opening prices...", open_time.strftime("%H:%M"))
        sleep_until(open_time)

    if not shutdown_event.is_set():
        # Give 5 seconds for opening tick data to arrive
        wait_seconds(5)
        record_prices(conn, "OPEN")

    # Wait for market close at 15:30
    close_time = ist_time(*CLOSE_RECORD_TIME)
    if now_ist() < close_time:
        log.info("Waiting until %s IST for closing prices... (background, checking every 60s)",
                 close_time.strftime("%H:%M"))
        sleep_until(close_time)

    if not shutdown_event.is_set():
        # Give 5 seconds for final ticks
        wait_seconds(5)
        record_prices(conn, "CLOSE")
        compute_day_regime(conn)

    # Cleanup
    log.info("Shutting down...")
    try:
        fyers_socket.close_connection()
    except Exception as e:
        log.warning("Error closing WebSocket: %s", e)

    conn.close()
    log.info("Pre-open collector finished for %s", date.today())


if __name__ == "__main__":
    main()
