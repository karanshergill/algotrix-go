#!/usr/bin/env python3
"""
Migrate tick_data from algotrix DB → atdb (nse_cm_ticks + nse_cm_depth_5).

Maps security_id → ISIN via symbols.dhan_token.
Packs 5-level bid/ask into JSONB for depth table.
Processes partition by partition to manage memory.
"""

import json
import psycopg2
import psycopg2.extras
from datetime import datetime

ALGOTRIX_DSN = "host=localhost dbname=algotrix user=me password=algotrix"
ATDB_DSN = "host=localhost dbname=atdb user=me password=algotrix"

PARTITIONS = [
    "20260224", "20260225", "20260226", "20260227",
    "20260302", "20260303", "20260304", "20260305", "20260306",
    "20260309", "20260310", "20260311", "20260313",
    "20260316", "20260317", "20260318", "20260319",
]

BATCH_SIZE = 50000


def load_isin_map():
    """dhan_token → isin from atdb.symbols"""
    conn = psycopg2.connect(ATDB_DSN)
    cur = conn.cursor()
    cur.execute("SELECT dhan_token, isin FROM symbols WHERE dhan_token IS NOT NULL")
    mapping = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return mapping


def migrate_partition(partition_date: str, isin_map: dict):
    table = f"tick_data_{partition_date}"
    print(f"\n{'='*80}")
    print(f"Migrating {table} ...")

    src = psycopg2.connect(ALGOTRIX_DSN)
    dst = psycopg2.connect(ATDB_DSN)

    # Server-side cursor for memory efficiency
    src_cur = src.cursor(name=f"read_{partition_date}")
    src_cur.itersize = BATCH_SIZE

    src_cur.execute(f"""
        SELECT ts, security_id, ltp, volume, open, high, low, close,
               total_buy_qty, total_sell_qty,
               bid_price_1, bid_qty_1, bid_orders_1,
               ask_price_1, ask_qty_1, ask_orders_1,
               bid_price_2, bid_qty_2, bid_orders_2,
               ask_price_2, ask_qty_2, ask_orders_2,
               bid_price_3, bid_qty_3, bid_orders_3,
               ask_price_3, ask_qty_3, ask_orders_3,
               bid_price_4, bid_qty_4, bid_orders_4,
               ask_price_4, ask_qty_4, ask_orders_4,
               bid_price_5, bid_qty_5, bid_orders_5,
               ask_price_5, ask_qty_5, ask_orders_5
        FROM {table}
        ORDER BY ts
    """)

    tick_batch = []
    depth_batch = []
    total_ticks = 0
    total_depth = 0
    skipped = 0

    dst_cur = dst.cursor()

    for row in src_cur:
        (ts, security_id, ltp, volume, open_, high, low, close_,
         tbq, tsq,
         bp1, bq1, bo1, ap1, aq1, ao1,
         bp2, bq2, bo2, ap2, aq2, ao2,
         bp3, bq3, bo3, ap3, aq3, ao3,
         bp4, bq4, bo4, ap4, aq4, ao4,
         bp5, bq5, bo5, ap5, aq5, ao5) = row

        isin = isin_map.get(security_id)
        if not isin:
            skipped += 1
            continue

        # nse_cm_ticks row
        tick_batch.append((
            ts, isin,
            float(ltp) if ltp else None,
            volume,
            float(open_) if open_ else None,
            float(high) if high else None,
            float(low) if low else None,
            None,  # prev_close (not in tick_data)
            None,  # change
            None,  # change_pct
        ))

        # nse_cm_depth_5 row — pack 5 levels into JSONB
        bids = []
        asks = []
        for bp, bq, bo, ap_, aq_, ao_ in [
            (bp1, bq1, bo1, ap1, aq1, ao1),
            (bp2, bq2, bo2, ap2, aq2, ao2),
            (bp3, bq3, bo3, ap3, aq3, ao3),
            (bp4, bq4, bo4, ap4, aq4, ao4),
            (bp5, bq5, bo5, ap5, aq5, ao5),
        ]:
            if bp and bq:
                bids.append({"price": float(bp), "qty": int(bq), "orders": int(bo or 0)})
            if ap_ and aq_:
                asks.append({"price": float(ap_), "qty": int(aq_), "orders": int(ao_ or 0)})

        if bids or asks:
            depth_batch.append((
                ts, isin,
                tbq, tsq,
                float(bp1) if bp1 else None,  # best_bid
                float(ap1) if ap1 else None,  # best_ask
                float(bq1) if bq1 else None,  # best_bid_qty
                float(aq1) if aq1 else None,  # best_ask_qty
                json.dumps(bids) if bids else None,
                json.dumps(asks) if asks else None,
            ))

        # Flush batches
        if len(tick_batch) >= BATCH_SIZE:
            psycopg2.extras.execute_values(
                dst_cur,
                """INSERT INTO nse_cm_ticks (timestamp, isin, ltp, volume, open, high, low, prev_close, change, change_pct)
                   VALUES %s ON CONFLICT DO NOTHING""",
                tick_batch, page_size=BATCH_SIZE
            )
            total_ticks += len(tick_batch)
            tick_batch = []

        if len(depth_batch) >= BATCH_SIZE:
            psycopg2.extras.execute_values(
                dst_cur,
                """INSERT INTO nse_cm_depth_5 (timestamp, isin, tbq, tsq, best_bid, best_ask, best_bid_qty, best_ask_qty, bids, asks)
                   VALUES %s ON CONFLICT DO NOTHING""",
                depth_batch, page_size=BATCH_SIZE
            )
            total_depth += len(depth_batch)
            depth_batch = []

            dst.commit()
            print(f"  ... {total_ticks:>12,} ticks, {total_depth:>12,} depth rows migrated, {skipped:,} skipped", flush=True)

    # Final flush
    if tick_batch:
        psycopg2.extras.execute_values(
            dst_cur,
            """INSERT INTO nse_cm_ticks (timestamp, isin, ltp, volume, open, high, low, prev_close, change, change_pct)
               VALUES %s ON CONFLICT DO NOTHING""",
            tick_batch, page_size=BATCH_SIZE
        )
        total_ticks += len(tick_batch)

    if depth_batch:
        psycopg2.extras.execute_values(
            dst_cur,
            """INSERT INTO nse_cm_depth_5 (timestamp, isin, tbq, tsq, best_bid, best_ask, best_bid_qty, best_ask_qty, bids, asks)
               VALUES %s ON CONFLICT DO NOTHING""",
            depth_batch, page_size=BATCH_SIZE
        )
        total_depth += len(depth_batch)

    dst.commit()
    dst_cur.close()
    src_cur.close()
    dst.close()
    src.close()

    print(f"  ✅ {table}: {total_ticks:,} ticks + {total_depth:,} depth rows migrated ({skipped:,} unmapped)")
    return total_ticks, total_depth


def main():
    print(f"Tick data migration: algotrix → atdb")
    print(f"Started: {datetime.now()}")
    print(f"Partitions: {len(PARTITIONS)}")

    isin_map = load_isin_map()
    print(f"ISIN mapping: {len(isin_map)} securities")

    grand_ticks = 0
    grand_depth = 0

    for p in PARTITIONS:
        t, d = migrate_partition(p, isin_map)
        grand_ticks += t
        grand_depth += d

    print(f"\n{'='*80}")
    print(f"MIGRATION COMPLETE")
    print(f"  Total ticks:  {grand_ticks:>15,}")
    print(f"  Total depth:  {grand_depth:>15,}")
    print(f"  Finished: {datetime.now()}")


if __name__ == "__main__":
    main()
