package feed

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"sync"

	"github.com/gorilla/websocket"
)

// Hub broadcasts JSON tick + depth messages to internal WebSocket clients.
// Binds to 127.0.0.1 ONLY — the Hono server is the sole consumer.
type Hub struct {
	port     int
	upgrader websocket.Upgrader
	clients  map[*websocket.Conn]chan []byte
	mu       sync.Mutex
	server   *http.Server
}

const hubChanSize = 256

func NewHub(port int) *Hub {
	if port <= 0 {
		port = 3002
	}
	return &Hub{
		port: port,
		upgrader: websocket.Upgrader{
			CheckOrigin: func(r *http.Request) bool { return true },
		},
		clients: make(map[*websocket.Conn]chan []byte),
	}
}

func (h *Hub) Start() error {
	addr := fmt.Sprintf("127.0.0.1:%d", h.port)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("hub listen %s: %w", addr, err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/", h.handleWS)
	h.server = &http.Server{Handler: mux}

	go func() {
		if err := h.server.Serve(ln); err != nil && err != http.ErrServerClosed {
			logTS("[Hub] serve error: %v", err)
		}
	}()

	logTS("[Hub] listening on %s", addr)
	return nil
}

func (h *Hub) handleWS(w http.ResponseWriter, r *http.Request) {
	conn, err := h.upgrader.Upgrade(w, r, nil)
	if err != nil {
		logTS("[Hub] upgrade error: %v", err)
		return
	}

	ch := make(chan []byte, hubChanSize)

	h.mu.Lock()
	h.clients[conn] = ch
	count := len(h.clients)
	h.mu.Unlock()

	logTS("[Hub] client connected (%d total)", count)

	// Writer goroutine: drains channel, sends to client.
	// Exits when channel is closed (by reader cleanup or Stop).
	go func() {
		for msg := range ch {
			if err := conn.WriteMessage(websocket.TextMessage, msg); err != nil {
				// Write failed — close conn to unblock the reader below.
				conn.Close()
				return
			}
		}
	}()

	// Block on reads to detect client disconnect.
	for {
		if _, _, err := conn.ReadMessage(); err != nil {
			break
		}
	}
	// Cleanup: remove from map, close channel (stops writer), close conn.
	// Cleanup: remove from map, close channel (stops writer), close conn.
	h.mu.Lock()
	ch2, ok := h.clients[conn]
	if ok {
		delete(h.clients, conn)
	}
	h.mu.Unlock()
	if ok && ch2 != nil {
		close(ch2)
	}
	conn.Close()
	logTS("[Hub] client disconnected (%d remaining)", h.clientCount())







	conn.Close()
	logTS("[Hub] client disconnected (%d remaining)", h.clientCount())
}

// Broadcast sends a pre-serialized JSON message to all connected clients.
// Non-blocking: drops oldest message if a client's channel is full.
func (h *Hub) Broadcast(msg []byte) {
	h.mu.Lock()
	defer h.mu.Unlock()

	for _, ch := range h.clients {
		select {
		case ch <- msg:
		default:
			// Channel full — drop oldest, enqueue new.
			select {
			case <-ch:
			default:
			}
			select {
			case ch <- msg:
			default:
			}
		}
	}
}

// BroadcastTick serializes a tick and broadcasts it.
func (h *Hub) BroadcastTick(symbol, isin string, row TickRow) {
	msg := map[string]interface{}{
		"type":   "tick",
		"symbol": symbol,
		"isin":   isin,
		"ts":     row.Timestamp.Unix(),
	}
	if row.Ltp != nil {
		msg["ltp"] = *row.Ltp
	}
	if row.Volume != nil {
		msg["volume"] = *row.Volume
	}
	if row.Open != nil {
		msg["open"] = *row.Open
	}
	if row.High != nil {
		msg["high"] = *row.High
	}
	if row.Low != nil {
		msg["low"] = *row.Low
	}
	if row.PrevClose != nil {
		msg["prevClose"] = *row.PrevClose
	}
	if row.Change != nil {
		msg["change"] = *row.Change
	}
	if row.ChangePct != nil {
		msg["changePct"] = *row.ChangePct
	}

	data, err := json.Marshal(msg)
	if err != nil {
		return
	}
	h.Broadcast(data)
}

// BroadcastDepth serializes a depth snapshot and broadcasts it.
func (h *Hub) BroadcastDepth(symbol, isin string, row DepthRow) {
	msg := map[string]interface{}{
		"type":    "depth",
		"symbol":  symbol,
		"isin":    isin,
		"bestBid": row.BidPrice1,
		"bestAsk": row.AskPrice1,
		"tbq":     row.TotalBuyQty,
		"tsq":     row.TotalSellQty,
		"ts":      row.Timestamp.Unix(),
	}

	data, err := json.Marshal(msg)
	if err != nil {
		return
	}
	h.Broadcast(data)
}

func (h *Hub) clientCount() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.clients)
}

func (h *Hub) Stop() {
	if h.server != nil {
		h.server.Close()
	}
	h.mu.Lock()
	for conn, ch := range h.clients {
		delete(h.clients, conn)
		close(ch)
		conn.Close()
	}
	h.mu.Unlock()
	logTS("[Hub] stopped")
}

// BroadcastSignal serializes a screener signal and broadcasts it.
func (h *Hub) BroadcastSignal(signal map[string]interface{}) {
	msg := map[string]interface{}{
		"type":   "signal",
		"signal": signal,
	}
	data, err := json.Marshal(msg)
	if err != nil {
		return
	}
	h.Broadcast(data)
}
