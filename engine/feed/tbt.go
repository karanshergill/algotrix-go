package feed

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	pb "github.com/karanshergill/algotrix-go/feed/proto"
	"github.com/gorilla/websocket"
	"github.com/jackc/pgx/v5/pgxpool"
	"google.golang.org/protobuf/proto"
)

const (
	tbtURLEndpoint   = "https://api-t1.fyers.in/indus/home/tbtws"
	tbtFallbackURL   = "wss://rtsocket-api.fyers.in/versova"
	symbolTokenAPI   = "https://api-t1.fyers.in/data/symbol-token"
	maxBackoffDelay  = 30 * time.Second
	initialBackoff   = 1 * time.Second
)

type TBTFeed struct {
	config  *Config
	token   string
	symbols []string
	conn    *websocket.Conn
	pool    *pgxpool.Pool
	writer  *PGWriter

	// Token ID → readable symbol mapping (e.g. "10100000002885" → "NSE:RELIANCE-EQ").
	tokenToSymbol map[string]string

	lastSnapshot map[string]time.Time
	mu           sync.Mutex
	stopOnce     sync.Once
	done         chan struct{}
	// connDone is closed when the current connection dies, causing readLoop + pingLoop to exit.
	connDone chan struct{}
	// reconnectFailed is closed if reconnection permanently fails.
	reconnectFailed chan struct{}
}

func NewTBTFeed(config *Config, token string, symbols []string, pool *pgxpool.Pool) *TBTFeed {
	return &TBTFeed{
		config:          config,
		token:           token,
		symbols:         symbols,
		pool:            pool,
		tokenToSymbol:   make(map[string]string),
		lastSnapshot:    make(map[string]time.Time),
		done:            make(chan struct{}),
		connDone:        make(chan struct{}),
		reconnectFailed: make(chan struct{}),
	}
}

// Fix 8: defer body.Close immediately after successful Do, before status check.
func getTBTURL(token string) string {
	req, err := http.NewRequest("GET", tbtURLEndpoint, nil)
	if err != nil {
		return tbtFallbackURL
	}
	req.Header.Set("Authorization", token)

	resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
	if err != nil {
		return tbtFallbackURL
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return tbtFallbackURL
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return tbtFallbackURL
	}

	var data struct {
		Data struct {
			SocketURL string `json:"socket_url"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &data); err != nil || data.Data.SocketURL == "" {
		return tbtFallbackURL
	}
	return data.Data.SocketURL
}

// Fix 4: Build token ID → symbol map via Fyers symbol-token API.
func (f *TBTFeed) buildTokenMap() error {
	payload, _ := json.Marshal(map[string]interface{}{
		"symbols": f.symbols,
	})

	req, err := http.NewRequest("POST", symbolTokenAPI, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("create symbol-token request: %w", err)
	}
	req.Header.Set("Authorization", f.token)
	req.Header.Set("Content-Type", "application/json")

	resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
	if err != nil {
		return fmt.Errorf("symbol-token API call: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read symbol-token response: %w", err)
	}

	var result struct {
		S            string            `json:"s"`
		ValidSymbol  map[string]string `json:"validSymbol"`
		Message      string            `json:"message"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return fmt.Errorf("parse symbol-token response: %w", err)
	}
	if result.S != "ok" {
		return fmt.Errorf("symbol-token API error: %s", result.Message)
	}

	for symbol, fytoken := range result.ValidSymbol {
		// The fytoken format is e.g. "101000000028850101".
		// The protobuf feed map key uses the full fytoken or a prefix.
		// Store both the full token and the exchange_token part (chars 10+).
		f.tokenToSymbol[fytoken] = symbol
		if len(fytoken) > 10 {
			exchangeToken := fytoken[10:]
			f.tokenToSymbol[exchangeToken] = symbol
		}
	}

	logTS("[TBT] token map built: %d symbols → %d token entries", len(f.symbols), len(f.tokenToSymbol))
	return nil
}

// resolveSymbol converts a protobuf ticker key to a readable symbol.
func (f *TBTFeed) resolveSymbol(ticker string) string {
	if sym, ok := f.tokenToSymbol[ticker]; ok {
		return sym
	}
	return ticker
}

func (f *TBTFeed) Start() error {
	cfg := f.config.Feed

	// Fix 4: Build token-to-symbol mapping.
	if err := f.buildTokenMap(); err != nil {
		logTS("[TBT] WARN: token map build failed: %v (will use raw token IDs)", err)
	}

	f.writer = NewPGWriter(f.pool, cfg.Storage.DepthTable, cfg.Storage.TicksTable, cfg.TBT.FlushIntervalMs, "TBT")

	// Connect WebSocket.
	if err := f.connect(); err != nil {
		return err
	}

	return nil
}

func (f *TBTFeed) connect() error {
	wsURL := getTBTURL(f.token)
	logTS("[TBT] connecting to %s", wsURL)

	dialer := websocket.Dialer{}
	headers := http.Header{}
	headers.Set("Authorization", f.token)

	conn, _, err := dialer.Dial(wsURL, headers)
	if err != nil {
		return fmt.Errorf("TBT connect: %w", err)
	}

	f.mu.Lock()
	f.conn = conn
	f.connDone = make(chan struct{})
	f.mu.Unlock()

	logTS("[TBT] websocket connected")

	// Subscribe.
	subMsg := map[string]interface{}{
		"type": 1,
		"data": map[string]interface{}{
			"subs":    1,
			"symbols": f.symbols,
			"mode":    "depth",
			"channel": "1",
		},
	}
	if err := conn.WriteJSON(subMsg); err != nil {
		return fmt.Errorf("TBT subscribe: %w", err)
	}

	// Resume channel.
	resumeMsg := map[string]interface{}{
		"type": 2,
		"data": map[string]interface{}{
			"resumeChannels": []string{"1"},
			"pauseChannels":  []string{},
		},
	}
	if err := conn.WriteJSON(resumeMsg); err != nil {
		return fmt.Errorf("TBT resume channel: %w", err)
	}

	logTS("[TBT] subscribed %d symbols in depth mode", len(f.symbols))

	// Fix 5: Both goroutines exit when connDone is closed.
	go f.readLoop()
	go f.pingLoop()

	return nil
}

func (f *TBTFeed) readLoop() {
	msgCount := 0
	defer func() {
		// Signal that the connection is dead.
		f.mu.Lock()
		select {
		case <-f.connDone:
		default:
			close(f.connDone)
		}
		f.mu.Unlock()

		// Fix 2: Attempt reconnection.
		f.tryReconnect()
	}()

	for {
		select {
		case <-f.done:
			return
		case <-f.connDone:
			return
		default:
		}

		msgType, message, err := f.conn.ReadMessage()
		if err != nil {
			select {
			case <-f.done:
				return // Graceful shutdown, don't reconnect.
			default:
			}
			logTS("[TBT] read error: %v", err)
			return
		}

		if msgType == websocket.TextMessage {
			logTS("[TBT] text: %s", string(message))
			continue
		}

		// Binary = protobuf.
		var sm pb.SocketMessage
		if err := proto.Unmarshal(message, &sm); err != nil {
			logTS("[TBT] protobuf decode error (%d bytes): %v", len(message), err)
			continue
		}

		if sm.Error {
			logTS("[TBT] server error: %s", sm.Msg)
			continue
		}

		for ticker, feed := range sm.Feeds {
			if feed.Depth == nil {
				continue
			}
			msgCount++
			// Fix 4: Resolve token ID to readable symbol.
			symbol := f.resolveSymbol(ticker)
			f.onDepthUpdate(symbol, feed, sm.Snapshot)

			if msgCount <= 5 {
				bidLevels := len(feed.Depth.Bids)
				askLevels := len(feed.Depth.Asks)
				var bestBid, bestAsk float64
				if bidLevels > 0 && feed.Depth.Bids[0].Price != nil {
					bestBid = float64(feed.Depth.Bids[0].Price.Value) / 100.0
				}
				if askLevels > 0 && feed.Depth.Asks[0].Price != nil {
					bestAsk = float64(feed.Depth.Asks[0].Price.Value) / 100.0
				}
				var tbq, tsq uint64
				if feed.Depth.Tbq != nil {
					tbq = feed.Depth.Tbq.Value
				}
				if feed.Depth.Tsq != nil {
					tsq = feed.Depth.Tsq.Value
				}
				logTS("[TBT] depth #%d: %s bid=%.2f ask=%.2f bids=%d asks=%d tbq=%d tsq=%d snapshot=%v",
					msgCount, symbol, bestBid, bestAsk, bidLevels, askLevels, tbq, tsq, sm.Snapshot)
			}
		}
	}
}

// Fix 2: Reconnection with exponential backoff.
func (f *TBTFeed) tryReconnect() {
	cfg := f.config.Feed.TBT
	if !cfg.Reconnect {
		logTS("[TBT] reconnection disabled, feed stopped")
		return
	}

	backoff := initialBackoff
	for attempt := 1; attempt <= cfg.MaxReconnectAttempts; attempt++ {
		select {
		case <-f.done:
			return // Shutdown requested.
		default:
		}

		logTS("[TBT] reconnect attempt %d/%d (backoff %v)", attempt, cfg.MaxReconnectAttempts, backoff)

		// Close old connection.
		f.mu.Lock()
		if f.conn != nil {
			f.conn.Close()
			f.conn = nil
		}
		f.mu.Unlock()

		time.Sleep(backoff)

		select {
		case <-f.done:
			return
		default:
		}

		if err := f.connect(); err != nil {
			logTS("[TBT] reconnect attempt %d failed: %v", attempt, err)
			backoff *= 2
			if backoff > maxBackoffDelay {
				backoff = maxBackoffDelay
			}
			continue
		}

		logTS("[TBT] reconnected successfully on attempt %d", attempt)
		return
	}

	logTS("[TBT] max reconnect attempts (%d) reached, feed stopped", cfg.MaxReconnectAttempts)
	close(f.reconnectFailed)
}

func (f *TBTFeed) onDepthUpdate(symbol string, feed *pb.MarketFeed, isSnapshot bool) {
	now := time.Now()
	intervalMs := f.config.Feed.TBT.SnapshotIntervalMs
	interval := time.Duration(intervalMs) * time.Millisecond

	f.mu.Lock()
	last, exists := f.lastSnapshot[symbol]
	if exists && !isSnapshot && now.Sub(last) < interval {
		f.mu.Unlock()
		return
	}
	f.lastSnapshot[symbol] = now
	f.mu.Unlock()

	if !exists {
		logTS("[TBT] first depth for %s", symbol)
	}

	depth := feed.Depth
	maxLevels := f.config.Feed.TBT.MaxDepthLevels
	ts := now

	var tbq, tsq int64
	if depth.Tbq != nil {
		tbq = int64(depth.Tbq.Value)
	}
	if depth.Tsq != nil {
		tsq = int64(depth.Tsq.Value)
	}

	// Fix 7: best_bid/ask top-level columns.
	var bestBid, bestAsk, bestBidQty, bestAskQty float64
	if len(depth.Bids) > 0 {
		if depth.Bids[0].Price != nil {
			bestBid = float64(depth.Bids[0].Price.Value) / 100.0
		}
		if depth.Bids[0].Qty != nil {
			bestBidQty = float64(depth.Bids[0].Qty.Value)
		}
	}
	if len(depth.Asks) > 0 {
		if depth.Asks[0].Price != nil {
			bestAsk = float64(depth.Asks[0].Price.Value) / 100.0
		}
		if depth.Asks[0].Qty != nil {
			bestAskQty = float64(depth.Asks[0].Qty.Value)
		}
	}

	// Build depth levels
	bids := make([]DepthLevel, 0, min(maxLevels, len(depth.Bids)))
	asks := make([]DepthLevel, 0, min(maxLevels, len(depth.Asks)))

	for i := 0; i < maxLevels && i < len(depth.Bids); i++ {
		bid := depth.Bids[i]
		var price, qty, orders float64
		if bid.Price != nil {
			price = float64(bid.Price.Value) / 100.0
		}
		if bid.Qty != nil {
			qty = float64(bid.Qty.Value)
		}
		if bid.Nord != nil {
			orders = float64(bid.Nord.Value)
		}
		bids = append(bids, DepthLevel{Price: price, Qty: qty, Orders: orders})
	}
	for i := 0; i < maxLevels && i < len(depth.Asks); i++ {
		ask := depth.Asks[i]
		var price, qty, orders float64
		if ask.Price != nil {
			price = float64(ask.Price.Value) / 100.0
		}
		if ask.Qty != nil {
			qty = float64(ask.Qty.Value)
		}
		if ask.Nord != nil {
			orders = float64(ask.Nord.Value)
		}
		asks = append(asks, DepthLevel{Price: price, Qty: qty, Orders: orders})
	}

	f.writer.WriteDepth(DepthRow{
		Ts:         ts,
		Symbol:     symbol,
		Tbq:        tbq,
		Tsq:        tsq,
		BestBid:    bestBid,
		BestAsk:    bestAsk,
		BestBidQty: bestBidQty,
		BestAskQty: bestAskQty,
		Bids:       bids,
		Asks:       asks,
	})
}

// Fix 5: pingLoop exits when connDone OR done is closed.
func (f *TBTFeed) pingLoop() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	f.mu.Lock()
	connDone := f.connDone
	f.mu.Unlock()

	for {
		select {
		case <-ticker.C:
			f.mu.Lock()
			conn := f.conn
			f.mu.Unlock()
			if conn != nil {
				if err := conn.WriteMessage(websocket.TextMessage, []byte("ping")); err != nil {
					logTS("[TBT] ping error: %v", err)
				}
			}
		case <-connDone:
			return
		case <-f.done:
			return
		}
	}
}

func (f *TBTFeed) Stop() {
	f.stopOnce.Do(func() {
		close(f.done)

		f.mu.Lock()
		if f.conn != nil {
			f.conn.Close()
		}
		select {
		case <-f.connDone:
		default:
			close(f.connDone)
		}
		f.mu.Unlock()

		logTS("[TBT] disconnected")

		if f.writer != nil {
			f.writer.Close()
		}
	})
}

// ReconnectFailed returns a channel that is closed if reconnection permanently fails.
func (f *TBTFeed) ReconnectFailed() <-chan struct{} {
	return f.reconnectFailed
}
