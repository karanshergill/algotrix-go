package feed

import (
	"context"
	"encoding/json"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// DepthLevel is one price level in the order book.
type DepthLevel struct {
	Price  float64 `json:"price"`
	Qty    float64 `json:"qty"`
	Orders float64 `json:"orders"`
}

// DepthRow is one depth snapshot row for nse_cm_depth.
type DepthRow struct {
	Timestamp   time.Time
	ISIN        string
	Tbq         int64
	Tsq         int64
	BestBid     float64
	BestAsk     float64
	BestBidQty  float64
	BestAskQty  float64
	Bids        []DepthLevel
	Asks        []DepthLevel
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
		bidsJSON, _ := json.Marshal(r.Bids)
		asksJSON, _ := json.Marshal(r.Asks)
		inputRows = append(inputRows, []interface{}{
			r.Timestamp, r.ISIN,
			r.Tbq, r.Tsq,
			r.BestBid, r.BestAsk, r.BestBidQty, r.BestAskQty,
			bidsJSON, asksJSON,
		})
	}

	cols := []string{"timestamp", "isin", "tbq", "tsq", "best_bid", "best_ask", "best_bid_qty", "best_ask_qty", "bids", "asks"}
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
