package feed

import (
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	fyersgosdk "github.com/FyersDev/fyers-go-sdk/websocket"
	"github.com/jackc/pgx/v5/pgxpool"
)

// TickCallback is called for every valid tick with ISIN-resolved data.
type TickCallback func(symbol, isin string, ltp float64, volume int64, ts time.Time)

type DataSocketFeed struct {
	config       *Config
	token        string
	symbols      []string
	socket       *fyersgosdk.FyersDataSocket
	pool         *pgxpool.Pool
	writer       *PGWriter
	hub          *Hub
	symbolToISIN map[string]string
	onTickCb     TickCallback // optional: called for every valid tick

	firstData    map[string]bool
	mu           sync.Mutex
	stopOnce     sync.Once
	done         chan struct{}
	reconnecting atomic.Bool
}

// SetOnTick registers a callback invoked for every valid tick.
func (f *DataSocketFeed) SetOnTick(cb TickCallback) { f.onTickCb = cb }

func NewDataSocketFeed(config *Config, token string, symbols []string, pool *pgxpool.Pool, symbolToISIN map[string]string, hub *Hub) *DataSocketFeed {
	return &DataSocketFeed{
		config:       config,
		token:        token,
		symbols:      symbols,
		pool:         pool,
		hub:          hub,
		symbolToISIN: symbolToISIN,
		firstData:    make(map[string]bool),
		done:         make(chan struct{}),
	}
}

func (f *DataSocketFeed) Start() error {
	cfg := f.config.Feed

	f.writer = NewPGWriter(f.pool, cfg.Storage.DepthTable, cfg.Storage.TicksTable, cfg.DataSocket.FlushIntervalMs, "DataSocket")

	// Connect — non-fatal if it fails (market closed, token expired, etc.).
	if err := f.connect(); err != nil {
		logTS("[DataSocket] initial connect failed: %v, will retry in background", err)
		go f.tryReconnect()
	}

	return nil
}

func (f *DataSocketFeed) connect() error {
	cfg := f.config.Feed

	f.mu.Lock()
	// Close any previous socket before creating a new one.
	if f.socket != nil {
		f.socket.CloseConnection()
		f.socket = nil
	}
	f.mu.Unlock()

	socket := fyersgosdk.NewFyersDataSocket(
		f.token,
		cfg.DataSocket.LogPath,
		false, // liteMode
		false, // writeToFile
		cfg.DataSocket.Reconnect,
		cfg.DataSocket.MaxReconnectAttempts,
		func() {
			logTS("[DataSocket] connected")
		},
		func(data fyersgosdk.DataClose) {
			logTS("[DataSocket] connection closed: %v", data)
			// Trigger reconnect if not already reconnecting and not shutting down.
			select {
			case <-f.done:
				return
			default:
			}
			if f.reconnecting.CompareAndSwap(false, true) {
				go f.tryReconnect()
			}
		},
		func(data fyersgosdk.DataError) {
			logTS("[DataSocket] error: %v", data)
		},
		f.onMessage,
	)

	if socket == nil {
		return fmt.Errorf("failed to create DataSocket (field mappings load failed)")
	}

	if err := socket.Connect(); err != nil {
		return fmt.Errorf("DataSocket connect: %w", err)
	}

	f.mu.Lock()
	f.socket = socket
	f.mu.Unlock()

	socket.Subscribe(f.symbols, "SymbolUpdate")
	logTS("[DataSocket] subscribed %d symbols", len(f.symbols))

	return nil
}

func (f *DataSocketFeed) tryReconnect() {
	defer f.reconnecting.Store(false)

	cfg := f.config.Feed.DataSocket
	if !cfg.Reconnect {
		logTS("[DataSocket] reconnect disabled, feed stopped")
		return
	}

	backoff := initialBackoff
	for attempt := 1; attempt <= cfg.MaxReconnectAttempts; attempt++ {
		select {
		case <-f.done:
			return
		default:
		}

		logTS("[DataSocket] reconnect attempt %d/%d (backoff %v)", attempt, cfg.MaxReconnectAttempts, backoff)

		time.Sleep(backoff)

		select {
		case <-f.done:
			return
		default:
		}

		if err := f.connect(); err != nil {
			logTS("[DataSocket] reconnect attempt %d failed: %v", attempt, err)
			backoff *= 2
			if backoff > maxBackoffDelay {
				backoff = maxBackoffDelay
			}
			continue
		}

		logTS("[DataSocket] reconnected successfully on attempt %d", attempt)
		return
	}

	logTS("[DataSocket] max reconnect attempts (%d) reached, feed stopped", cfg.MaxReconnectAttempts)
}

func (f *DataSocketFeed) Stop() {
	f.stopOnce.Do(func() {
		close(f.done)
		if f.socket != nil {
			f.socket.CloseConnection()
			logTS("[DataSocket] disconnected")
		}
		if f.writer != nil {
			f.writer.Close()
		}
	})
}

func (f *DataSocketFeed) onMessage(resp fyersgosdk.DataResponse) {
	data := map[string]interface{}(resp)

	// Skip non-data messages (subscribe confirmations, etc.)
	msgType, _ := data["type"].(string)
	if msgType != "sf" && msgType != "scrips" {
		return
	}

	symbol, _ := data["symbol"].(string)
	if symbol == "" {
		return
	}

	isin, ok := f.symbolToISIN[symbol]
	if !ok {
		logTS("[DataSocket] WARN: no ISIN for symbol %s, skipping row", symbol)
		return
	}

	f.mu.Lock()
	if !f.firstData[symbol] {
		f.firstData[symbol] = true
		f.mu.Unlock()
		logTS("[DataSocket] first data received for %s (%s)", symbol, isin)
	} else {
		f.mu.Unlock()
	}

	ts := time.Now()

	ltp, ltpOk := asFloat64(data["ltp"])
	vol, volOk := asInt64(data["vol_traded_today"])
	openP, openOk := asFloat64(data["open_price"])
	highP, highOk := asFloat64(data["high_price"])
	lowP, lowOk := asFloat64(data["low_price"])
	prevClose, prevCloseOk := asFloat64(data["prev_close_price"])
	ch, chOk := asFloat64(data["ch"])
	chp, chpOk := asFloat64(data["chp"])

	row := TickRow{Timestamp: ts, ISIN: isin}
	if ltpOk {
		row.Ltp = &ltp
	}
	if volOk {
		row.Volume = &vol
	}
	if openOk {
		row.Open = &openP
	}
	if highOk {
		row.High = &highP
	}
	if lowOk {
		row.Low = &lowP
	}
	if prevCloseOk {
		row.PrevClose = &prevClose
	}
	if chOk {
		row.Change = &ch
	}
	if chpOk {
		row.ChangePct = &chp
	}

	f.writer.WriteTick(row)

	if f.hub != nil {
		f.hub.BroadcastTick(symbol, isin, row)
	}

	// Feature engine callback
	if f.onTickCb != nil && ltpOk && volOk {
		f.onTickCb(symbol, isin, ltp, vol, ts)
	}
}

// Fix 6: No reflection. Exhaustive type matching including SDK's FloatSDK.
func asFloat64(v interface{}) (float64, bool) {
	if v == nil {
		return 0, false
	}
	switch val := v.(type) {
	case float64:
		return val, true
	case float32:
		return float64(val), true
	case int32:
		return float64(val), true
	case int64:
		return float64(val), true
	case int:
		return float64(val), true
	case uint32:
		return float64(val), true
	case uint64:
		return float64(val), true
	case fyersgosdk.FloatSDK:
		return float64(val), true
	default:
		return 0, false
	}
}

func asInt64(v interface{}) (int64, bool) {
	if v == nil {
		return 0, false
	}
	switch val := v.(type) {
	case int64:
		return val, true
	case int32:
		return int64(val), true
	case int:
		return int64(val), true
	case float64:
		return int64(val), true
	case float32:
		return int64(val), true
	case uint32:
		return int64(val), true
	case uint64:
		return int64(val), true
	case fyersgosdk.FloatSDK:
		return int64(val), true
	default:
		return 0, false
	}
}
