package feed

import (
	"context"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	qdb "github.com/questdb/go-questdb-client/v4"
)

// ILPWriter is a thread-safe, batching ILP writer. All ILP writes go through
// a single goroutine to avoid concurrent access to the LineSender. Flushes
// happen on a configurable interval (not per-message).
type ILPWriter struct {
	sender    qdb.LineSender
	writeCh   chan func(qdb.LineSender)
	done      chan struct{}
	wg        sync.WaitGroup
	closeOnce sync.Once
	name      string
	closed    int32 // atomic: 1 = closed
}

// NewILPWriter creates and starts a writer. flushIntervalMs controls how often
// buffered rows are flushed to QuestDB. name is used for log prefixes.
func NewILPWriter(host string, port int, flushIntervalMs int, name string) (*ILPWriter, error) {
	ctx := context.Background()
	addr := fmt.Sprintf("%s:%d", host, port)
	sender, err := qdb.LineSenderFromConf(ctx, fmt.Sprintf("tcp::addr=%s;", addr))
	if err != nil {
		return nil, fmt.Errorf("questdb ILP connect: %w", err)
	}

	if flushIntervalMs <= 0 {
		flushIntervalMs = 100
	}

	w := &ILPWriter{
		sender:  sender,
		writeCh: make(chan func(qdb.LineSender), 8192),
		done:    make(chan struct{}),
		name:    name,
	}

	w.wg.Add(1)
	go w.loop(time.Duration(flushIntervalMs) * time.Millisecond)
	return w, nil
}

func (w *ILPWriter) loop(flushInterval time.Duration) {
	defer w.wg.Done()
	ticker := time.NewTicker(flushInterval)
	defer ticker.Stop()

	dirty := false
	for {
		select {
		case fn := <-w.writeCh:
			fn(w.sender)
			dirty = true
		case <-ticker.C:
			if dirty {
				w.flush()
				dirty = false
			}
		case <-w.done:
			// Drain remaining writes in channel buffer.
			for {
				select {
				case fn := <-w.writeCh:
					fn(w.sender)
					dirty = true
				default:
					if dirty {
						w.flush()
					}
					return
				}
			}
		}
	}
}

func (w *ILPWriter) flush() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := w.sender.Flush(ctx); err != nil {
		logTS("[%s] ILP flush error: %v", w.name, err)
	}
}

// Write enqueues a write function. The function will be called from the writer
// goroutine with exclusive access to the LineSender. Non-blocking if the
// channel has capacity; drops on overflow with a log warning.
func (w *ILPWriter) Write(fn func(qdb.LineSender)) {
	if atomic.LoadInt32(&w.closed) == 1 {
		return
	}
	select {
	case w.writeCh <- fn:
	default:
		logTS("[%s] ILP write channel full, dropping row", w.name)
	}
}

// Close stops the writer, flushes remaining data, and closes the sender.
func (w *ILPWriter) Close() {
	w.closeOnce.Do(func() {
		atomic.StoreInt32(&w.closed, 1)
		close(w.done)
		w.wg.Wait()
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		w.sender.Close(ctx)
	})
}
