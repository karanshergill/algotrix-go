package feed

import (
	"context"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// DepthRow is one depth snapshot row for nse_cm_depth (flat columns).
type DepthRow struct {
	Timestamp  time.Time
	ISIN       string
	TotalBuyQty  int64
	TotalSellQty int64
	BidPrice1  float32; BidQty1  int32; BidOrders1  int16
	AskPrice1  float32; AskQty1  int32; AskOrders1  int16
	BidPrice2  float32; BidQty2  int32; BidOrders2  int16
	AskPrice2  float32; AskQty2  int32; AskOrders2  int16
	BidPrice3  float32; BidQty3  int32; BidOrders3  int16
	AskPrice3  float32; AskQty3  int32; AskOrders3  int16
	BidPrice4  float32; BidQty4  int32; BidOrders4  int16
	AskPrice4  float32; AskQty4  int32; AskOrders4  int16
	BidPrice5  float32; BidQty5  int32; BidOrders5  int16
	AskPrice5  float32; AskQty5  int32; AskOrders5  int16
}

// TickRow is one tick row for nse_cm_ticks.
type TickRow struct {
	Timestamp time.Time
	ISIN      string
	Ltp       *float64
	Volume    *int64
	Open      *float64
	High      *float64
	Low       *float64
	PrevClose *float64
	Change    *float64
	ChangePct *float64
}

// PGWriter batches DepthRow and TickRow writes to PostgreSQL/TimescaleDB.
// A single background goroutine drains the channels and bulk-inserts via
// pgx COPY or multi-row INSERT on a configurable flush interval.
type PGWriter struct {
	pool          *pgxpool.Pool
	depthCh       chan DepthRow
	tickCh        chan TickRow
	done          chan struct{}
	wg            sync.WaitGroup
	closeOnce     sync.Once
	closed        int32
	depthTable    string
	ticksTable    string
	flushInterval time.Duration
	name          string
}

// NewPGWriter creates and starts a writer that flushes every flushIntervalMs ms.
func NewPGWriter(pool *pgxpool.Pool, depthTable, ticksTable string, flushIntervalMs int, name string) *PGWriter {
	if flushIntervalMs <= 0 {
		flushIntervalMs = 200
	}
	w := &PGWriter{
		pool:          pool,
		depthCh:       make(chan DepthRow, 8192),
		tickCh:        make(chan TickRow, 8192),
		done:          make(chan struct{}),
		depthTable:    depthTable,
		ticksTable:    ticksTable,
		flushInterval: time.Duration(flushIntervalMs) * time.Millisecond,
		name:          name,
	}
	w.wg.Add(1)
	go w.loop()
	return w
}

func (w *PGWriter) WriteDepth(row DepthRow) {
	if atomic.LoadInt32(&w.closed) == 1 {
		return
	}
	select {
	case w.depthCh <- row:
	default:
		logTS("[%s] depth channel full, dropping row for %s", w.name, row.ISIN)
	}
}

func (w *PGWriter) WriteTick(row TickRow) {
	if atomic.LoadInt32(&w.closed) == 1 {
		return
	}
	select {
	case w.tickCh <- row:
	default:
		logTS("[%s] tick channel full, dropping row for %s", w.name, row.ISIN)
	}
}

func (w *PGWriter) loop() {
	defer w.wg.Done()
	ticker := time.NewTicker(w.flushInterval)
	defer ticker.Stop()

	var depths []DepthRow
	var ticks []TickRow

	for {
		select {
		case row := <-w.depthCh:
			depths = append(depths, row)
		case row := <-w.tickCh:
			ticks = append(ticks, row)
		case <-ticker.C:
			if len(depths) > 0 {
				w.flushDepth(depths)
				depths = depths[:0]
			}
			if len(ticks) > 0 {
				w.flushTicks(ticks)
				ticks = ticks[:0]
			}
		case <-w.done:
			// Drain remaining
			for {
				select {
				case row := <-w.depthCh:
					depths = append(depths, row)
				case row := <-w.tickCh:
					ticks = append(ticks, row)
				default:
					if len(depths) > 0 {
						w.flushDepth(depths)
					}
					if len(ticks) > 0 {
						w.flushTicks(ticks)
					}
					return
				}
			}
		}
	}
}

func (w *PGWriter) flushDepth(rows []DepthRow) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	inputRows := make([][]interface{}, 0, len(rows))
	for _, r := range rows {
		inputRows = append(inputRows, []interface{}{
			r.Timestamp, r.ISIN,
			r.TotalBuyQty, r.TotalSellQty,
			r.BidPrice1, r.BidQty1, r.BidOrders1,
			r.AskPrice1, r.AskQty1, r.AskOrders1,
			r.BidPrice2, r.BidQty2, r.BidOrders2,
			r.AskPrice2, r.AskQty2, r.AskOrders2,
			r.BidPrice3, r.BidQty3, r.BidOrders3,
			r.AskPrice3, r.AskQty3, r.AskOrders3,
			r.BidPrice4, r.BidQty4, r.BidOrders4,
			r.AskPrice4, r.AskQty4, r.AskOrders4,
			r.BidPrice5, r.BidQty5, r.BidOrders5,
			r.AskPrice5, r.AskQty5, r.AskOrders5,
		})
	}

	cols := []string{
		"timestamp", "isin", "total_buy_qty", "total_sell_qty",
		"bid_price_1", "bid_qty_1", "bid_orders_1",
		"ask_price_1", "ask_qty_1", "ask_orders_1",
		"bid_price_2", "bid_qty_2", "bid_orders_2",
		"ask_price_2", "ask_qty_2", "ask_orders_2",
		"bid_price_3", "bid_qty_3", "bid_orders_3",
		"ask_price_3", "ask_qty_3", "ask_orders_3",
		"bid_price_4", "bid_qty_4", "bid_orders_4",
		"ask_price_4", "ask_qty_4", "ask_orders_4",
		"bid_price_5", "bid_qty_5", "bid_orders_5",
		"ask_price_5", "ask_qty_5", "ask_orders_5",
	}
	n, err := w.pool.CopyFrom(
		ctx,
		pgx.Identifier{w.depthTable},
		cols,
		pgx.CopyFromRows(inputRows),
	)
	if err != nil {
		logTS("[%s] depth flush error (%d rows): %v", w.name, len(rows), err)
		return
	}
	if n != int64(len(rows)) {
		logTS("[%s] depth flush: expected %d rows, got %d", w.name, len(rows), n)
	}
}

func (w *PGWriter) flushTicks(rows []TickRow) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	inputRows := make([][]interface{}, 0, len(rows))
	for _, r := range rows {
		inputRows = append(inputRows, []interface{}{
			r.Timestamp, r.ISIN,
			r.Ltp, r.Volume,
			r.Open, r.High, r.Low, r.PrevClose,
			r.Change, r.ChangePct,
		})
	}

	cols := []string{"timestamp", "isin", "ltp", "volume", "open", "high", "low", "prev_close", "change", "change_pct"}
	n, err := w.pool.CopyFrom(
		ctx,
		pgx.Identifier{w.ticksTable},
		cols,
		pgx.CopyFromRows(inputRows),
	)
	if err != nil {
		logTS("[%s] tick flush error (%d rows): %v", w.name, len(rows), err)
		return
	}
	if n != int64(len(rows)) {
		logTS("[%s] tick flush: expected %d rows, got %d", w.name, len(rows), n)
	}
}

func (w *PGWriter) Close() {
	w.closeOnce.Do(func() {
		atomic.StoreInt32(&w.closed, 1)
		close(w.done)
		w.wg.Wait()
	})
}
