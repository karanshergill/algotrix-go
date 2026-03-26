package feed

import (
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	fyersgosdk "github.com/FyersDev/fyers-go-sdk/websocket"
	"github.com/jackc/pgx/v5/pgxpool"
)

// TickData is the normalized SymbolUpdate payload passed through the tick pipeline.
type TickData struct {
	Symbol         string
	ISIN           string
	LTP            float64
	Volume         int64
	TS             time.Time
	OpenPrice      float64
	HighPrice      float64
	LowPrice       float64
	PrevClosePrice float64
	Change         float64
	ChangePct      float64
	TotBuyQty      int64
	TotSellQty     int64
	BidPrice       float64
	AskPrice       float64
	BidSize        int64
	AskSize        int64
	AvgTradePrice  float64
	LastTradedQty  int64
	LastTradedTime int64
	ExchFeedTime   int64
	OI             int64
	YearHigh       float64
	YearLow        float64
	LowerCircuit   float64
	UpperCircuit   float64
}

// TickCallback is called for every valid tick with ISIN-resolved data.
type TickCallback func(data TickData)

// DepthCallback is called for every valid depth update with ISIN-resolved data.
type DepthCallback func(isin string, bids, asks [5]struct{ Price float64; Qty int64 }, ts time.Time)

type DataSocketFeed struct {
	config       *Config
	token        string
	symbols      []string
	socket       *fyersgosdk.FyersDataSocket // tick connection (SymbolUpdate)
	depthSocket  *fyersgosdk.FyersDataSocket // depth connection (DepthUpdate) — separate to avoid topic cap
	pool         *pgxpool.Pool
	writer       *PGWriter
	hub          *Hub
	symbolToISIN map[string]string
	onTickCb     TickCallback  // optional: called for every valid tick
	onDepthCb    DepthCallback // optional: called for every valid depth update

	firstData    map[string]bool
	mu           sync.Mutex
	stopOnce     sync.Once
	done         chan struct{}
	reconnecting atomic.Bool
}

// SetOnTick registers a callback invoked for every valid tick.
func (f *DataSocketFeed) SetOnTick(cb TickCallback) { f.onTickCb = cb }

// SetOnDepth registers a callback invoked for every valid depth update.
func (f *DataSocketFeed) SetOnDepth(cb DepthCallback) { f.onDepthCb = cb }

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
	if f.depthSocket != nil {
		f.depthSocket.CloseConnection()
		f.depthSocket = nil
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
	logTS("[DataSocket] subscribed %d symbols for ticks", len(f.symbols))

	// --- Separate depth connection to avoid per-connection topic cap ---
	// Fyers caps ~1350 total topics per WebSocket. With 864 sf| + 864 dp| = 1728,
	// depth gets partially dropped. Solution: dedicated depth socket.
	depthSocket := fyersgosdk.NewFyersDataSocket(
		f.token,
		cfg.DataSocket.LogPath,
		false, // liteMode
		false, // writeToFile
		cfg.DataSocket.Reconnect,
		cfg.DataSocket.MaxReconnectAttempts,
		func() {
			logTS("[DataSocket:Depth] connected")
		},
		func(data fyersgosdk.DataClose) {
			logTS("[DataSocket:Depth] connection closed: %v", data)
		},
		func(data fyersgosdk.DataError) {
			logTS("[DataSocket:Depth] error: %v", data)
		},
		f.onDepthOnlyMessage, // depth-only message handler
	)

	if depthSocket == nil {
		logTS("[DataSocket:Depth] failed to create depth socket, depth will be partial")
		return nil
	}

	if err := depthSocket.Connect(); err != nil {
		logTS("[DataSocket:Depth] connect failed: %v, depth will be partial", err)
		return nil
	}

	f.mu.Lock()
	f.depthSocket = depthSocket
	f.mu.Unlock()

	depthSocket.Subscribe(f.symbols, "DepthUpdate")
	logTS("[DataSocket:Depth] subscribed %d symbols for depth on dedicated connection", len(f.symbols))

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
		if f.depthSocket != nil {
			f.depthSocket.CloseConnection()
			logTS("[DataSocket:Depth] disconnected")
		}
		if f.writer != nil {
			f.writer.Close()
		}
	})
}

// onDepthOnlyMessage handles messages from the dedicated depth socket.
func (f *DataSocketFeed) onDepthOnlyMessage(resp fyersgosdk.DataResponse) {
	data := map[string]interface{}(resp)
	msgType, _ := data["type"].(string)
	if msgType == "dp" {
		f.onDepthMessage(data)
	}
}

func (f *DataSocketFeed) onMessage(resp fyersgosdk.DataResponse) {
	data := map[string]interface{}(resp)

	// Skip non-data messages (subscribe confirmations, etc.)
	msgType, _ := data["type"].(string)

	switch msgType {
	case "sf", "scrips":
		f.onTickMessage(data)
	case "dp":
		f.onDepthMessage(data)
	}
}

func (f *DataSocketFeed) onTickMessage(data map[string]interface{}) {
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
	tick := TickData{
		Symbol: symbol,
		ISIN:   isin,
		TS:     ts,
	}

	ltp, ltpOk := asFloat64(data["ltp"])
	vol, volOk := asInt64(data["vol_traded_today"])
	openP, openOk := asFloat64(data["open_price"])
	highP, highOk := asFloat64(data["high_price"])
	lowP, lowOk := asFloat64(data["low_price"])
	prevClose, prevCloseOk := asFloat64(data["prev_close_price"])
	ch, chOk := asFloat64(data["ch"])
	chp, chpOk := asFloat64(data["chp"])
	totBuyQty, totBuyQtyOk := asInt64(data["tot_buy_qty"])
	totSellQty, totSellQtyOk := asInt64(data["tot_sell_qty"])
	bidPrice, bidPriceOk := asFloat64(data["bid_price"])
	askPrice, askPriceOk := asFloat64(data["ask_price"])
	bidSize, bidSizeOk := asInt64(data["bid_size"])
	askSize, askSizeOk := asInt64(data["ask_size"])
	avgTradePrice, avgTradePriceOk := asFloat64(data["avg_trade_price"])
	lastTradedQty, lastTradedQtyOk := asInt64(data["last_traded_qty"])
	lastTradedTime, lastTradedTimeOk := asInt64(data["last_traded_time"])
	exchFeedTime, exchFeedTimeOk := asInt64(data["exch_feed_time"])
	oi, oiOk := asInt64(data["OI"])
	yearHigh, yearHighOk := asFloat64(data["Yhigh"])
	yearLow, yearLowOk := asFloat64(data["Ylow"])
	lowerCircuit, lowerCircuitOk := asFloat64(data["lower_ckt"])
	upperCircuit, upperCircuitOk := asFloat64(data["upper_ckt"])

	row := TickRow{Timestamp: ts, ISIN: isin}
	if ltpOk {
		tick.LTP = ltp
		row.Ltp = &ltp
	}
	if volOk {
		tick.Volume = vol
		row.Volume = &vol
	}
	if openOk {
		tick.OpenPrice = openP
		row.Open = &openP
	}
	if highOk {
		tick.HighPrice = highP
		row.High = &highP
	}
	if lowOk {
		tick.LowPrice = lowP
		row.Low = &lowP
	}
	if prevCloseOk {
		tick.PrevClosePrice = prevClose
		row.PrevClose = &prevClose
	}
	if chOk {
		tick.Change = ch
		row.Change = &ch
	}
	if chpOk {
		tick.ChangePct = chp
		row.ChangePct = &chp
	}
	if totBuyQtyOk {
		tick.TotBuyQty = totBuyQty
		row.TotalBuyQty = &totBuyQty
	}
	if totSellQtyOk {
		tick.TotSellQty = totSellQty
		row.TotalSellQty = &totSellQty
	}
	if bidPriceOk {
		tick.BidPrice = bidPrice
		row.BidPrice1 = &bidPrice
	}
	if askPriceOk {
		tick.AskPrice = askPrice
		row.AskPrice1 = &askPrice
	}
	if bidSizeOk {
		tick.BidSize = bidSize
		row.BidQty1 = &bidSize
	}
	if askSizeOk {
		tick.AskSize = askSize
		row.AskQty1 = &askSize
	}
	if avgTradePriceOk {
		tick.AvgTradePrice = avgTradePrice
		row.AvgPrice = &avgTradePrice
	}
	if lastTradedQtyOk {
		tick.LastTradedQty = lastTradedQty
		row.Ltq = &lastTradedQty
	}
	if lastTradedTimeOk {
		tick.LastTradedTime = lastTradedTime
	}
	if exchFeedTimeOk {
		tick.ExchFeedTime = exchFeedTime
	}
	if oiOk {
		tick.OI = oi
		row.Oi = &oi
	}
	if yearHighOk {
		tick.YearHigh = yearHigh
	}
	if yearLowOk {
		tick.YearLow = yearLow
	}
	if lowerCircuitOk {
		tick.LowerCircuit = lowerCircuit
		row.LowerCircuit = &lowerCircuit
	}
	if upperCircuitOk {
		tick.UpperCircuit = upperCircuit
		row.UpperCircuit = &upperCircuit
	}

	f.writer.WriteTick(row)

	if f.hub != nil {
		f.hub.BroadcastTick(symbol, isin, row)
	}

	// Feature engine callback
	if f.onTickCb != nil && ltpOk && volOk {
		f.onTickCb(tick)
	}
}

func (f *DataSocketFeed) onDepthMessage(data map[string]interface{}) {
	symbol, _ := data["symbol"].(string)
	if symbol == "" {
		return
	}

	isin, ok := f.symbolToISIN[symbol]
	if !ok {
		return
	}

	ts := time.Now()

	// Extract 5-level depth from Fyers dp message.
	row := DepthRow{Timestamp: ts, ISIN: isin}

	// Total buy/sell quantities (top-level fields if available).
	if v, ok := asInt64(data["total_buy_qty"]); ok {
		row.TotalBuyQty = v
	}
	if v, ok := asInt64(data["total_sell_qty"]); ok {
		row.TotalSellQty = v
	}

	bidPrices := [5]*float32{&row.BidPrice1, &row.BidPrice2, &row.BidPrice3, &row.BidPrice4, &row.BidPrice5}
	bidQtys := [5]*int32{&row.BidQty1, &row.BidQty2, &row.BidQty3, &row.BidQty4, &row.BidQty5}
	bidOrders := [5]*int16{&row.BidOrders1, &row.BidOrders2, &row.BidOrders3, &row.BidOrders4, &row.BidOrders5}
	askPrices := [5]*float32{&row.AskPrice1, &row.AskPrice2, &row.AskPrice3, &row.AskPrice4, &row.AskPrice5}
	askQtys := [5]*int32{&row.AskQty1, &row.AskQty2, &row.AskQty3, &row.AskQty4, &row.AskQty5}
	askOrders := [5]*int16{&row.AskOrders1, &row.AskOrders2, &row.AskOrders3, &row.AskOrders4, &row.AskOrders5}

	hasAny := false
	for i := 1; i <= 5; i++ {
		suffix := fmt.Sprintf("%d", i)
		idx := i - 1

		if bp, ok := asFloat64(data["bid_price"+suffix]); ok {
			*bidPrices[idx] = float32(bp)
			hasAny = true
		}
		if bs, ok := asFloat64(data["bid_size"+suffix]); ok {
			*bidQtys[idx] = int32(bs)
			hasAny = true
		}
		if bo, ok := asFloat64(data["bid_order"+suffix]); ok {
			*bidOrders[idx] = int16(bo)
		}

		if ap, ok := asFloat64(data["ask_price"+suffix]); ok {
			*askPrices[idx] = float32(ap)
			hasAny = true
		}
		if as, ok := asFloat64(data["ask_size"+suffix]); ok {
			*askQtys[idx] = int32(as)
			hasAny = true
		}
		if ao, ok := asFloat64(data["ask_order"+suffix]); ok {
			*askOrders[idx] = int16(ao)
		}
	}

	if !hasAny {
		return
	}

	// Sum quantities for total_buy/sell if not provided at top level.
	if row.TotalBuyQty == 0 {
		for _, q := range bidQtys {
			row.TotalBuyQty += int64(*q)
		}
	}
	if row.TotalSellQty == 0 {
		for _, q := range askQtys {
			row.TotalSellQty += int64(*q)
		}
	}

	f.writer.WriteDepth(row)

	// Feature engine depth callback
	if f.onDepthCb != nil && hasAny {
		var bids, asks [5]struct{ Price float64; Qty int64 }
		for i := 0; i < 5; i++ {
			bids[i].Price = float64(*bidPrices[i])
			bids[i].Qty = int64(*bidQtys[i])
			asks[i].Price = float64(*askPrices[i])
			asks[i].Qty = int64(*askQtys[i])
		}
		f.onDepthCb(isin, bids, asks, ts)
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
