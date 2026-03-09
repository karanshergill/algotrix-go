package feed

import (
	"context"
	"fmt"
	"sync"
	"time"

	fyersgosdk "github.com/FyersDev/fyers-go-sdk/websocket"
	qdb "github.com/questdb/go-questdb-client/v4"
)

type DataSocketFeed struct {
	config  *Config
	token   string
	symbols []string
	socket  *fyersgosdk.FyersDataSocket
	writer  *ILPWriter

	firstData map[string]bool
	mu        sync.Mutex
	stopOnce  sync.Once
	done      chan struct{}
}

func NewDataSocketFeed(config *Config, token string, symbols []string) *DataSocketFeed {
	return &DataSocketFeed{
		config:    config,
		token:     token,
		symbols:   symbols,
		firstData: make(map[string]bool),
		done:      make(chan struct{}),
	}
}

func (f *DataSocketFeed) Start() error {
	cfg := f.config.Feed

	// Fix 1+3: Single-writer ILP with periodic flush.
	writer, err := NewILPWriter(
		cfg.Storage.QuestDBILPHost,
		cfg.Storage.QuestDBILPPort,
		cfg.DataSocket.FlushIntervalMs,
		"DataSocket",
	)
	if err != nil {
		return err
	}
	f.writer = writer

	f.socket = fyersgosdk.NewFyersDataSocket(
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
		},
		func(data fyersgosdk.DataError) {
			logTS("[DataSocket] error: %v", data)
		},
		f.onMessage,
	)

	if f.socket == nil {
		return fmt.Errorf("failed to create DataSocket (field mappings load failed)")
	}

	if err := f.socket.Connect(); err != nil {
		return fmt.Errorf("DataSocket connect: %w", err)
	}

	f.socket.Subscribe(f.symbols, "SymbolUpdate")
	logTS("[DataSocket] subscribed %d symbols", len(f.symbols))

	return nil
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

	f.mu.Lock()
	if !f.firstData[symbol] {
		f.firstData[symbol] = true
		f.mu.Unlock()
		logTS("[DataSocket] first data received for %s", symbol)
	} else {
		f.mu.Unlock()
	}

	table := f.config.Feed.Storage.TicksTable
	ts := time.Now()

	// Extract values before closure to avoid data race.
	ltp, ltpOk := asFloat64(data["ltp"])
	vol, volOk := asInt64(data["vol_traded_today"])
	openP, openOk := asFloat64(data["open_price"])
	highP, highOk := asFloat64(data["high_price"])
	lowP, lowOk := asFloat64(data["low_price"])
	prevClose, prevCloseOk := asFloat64(data["prev_close_price"])
	ch, chOk := asFloat64(data["ch"])
	chp, chpOk := asFloat64(data["chp"])

	// Fix 1+3: Enqueue write to single-writer goroutine.
	f.writer.Write(func(sender qdb.LineSender) {
		ctx := context.Background()

		sender.Table(table).
			Symbol("symbol", symbol)

		if ltpOk {
			sender.Float64Column("ltp", ltp)
		}
		if volOk {
			sender.Int64Column("volume", vol)
		}
		if openOk {
			sender.Float64Column("open", openP)
		}
		if highOk {
			sender.Float64Column("high", highP)
		}
		if lowOk {
			sender.Float64Column("low", lowP)
		}
		if prevCloseOk {
			sender.Float64Column("prev_close", prevClose)
		}
		if chOk {
			sender.Float64Column("change", ch)
		}
		if chpOk {
			sender.Float64Column("change_pct", chp)
		}

		if err := sender.At(ctx, ts); err != nil {
			logTS("[DataSocket] ILP write error for %s: %v", symbol, err)
		}
	})
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
