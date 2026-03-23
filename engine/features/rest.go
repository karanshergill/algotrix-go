package features

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
)

// RESTServer serves feature snapshots over HTTP.
// All reads come from the immutable snapshot — never touches live state.
type RESTServer struct {
	engine *FeatureEngine
	port   int
}

// NewRESTServer creates a REST server bound to the given engine and port.
func NewRESTServer(engine *FeatureEngine, port int) *RESTServer {
	return &RESTServer{engine: engine, port: port}
}

// Start launches the HTTP server and blocks until ctx is cancelled.
func (r *RESTServer) Start(ctx context.Context) error {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /features/market", r.handleMarket)
	mux.HandleFunc("GET /features/meta", r.handleMeta)
	mux.HandleFunc("GET /features/sector/{name}", r.handleSector)
	mux.HandleFunc("GET /features/{isin}", r.handleStock)
	mux.HandleFunc("GET /features", r.handleAllFeatures)

	server := &http.Server{
		Handler: mux,
	}

	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", r.port))
	if err != nil {
		return fmt.Errorf("rest listen: %w", err)
	}

	go func() {
		<-ctx.Done()
		server.Close()
	}()

	err = server.Serve(ln)
	if err == http.ErrServerClosed {
		return nil
	}
	return err
}

// Handler returns the http.Handler for use with httptest (no need to bind a port).
func (r *RESTServer) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /features/market", r.handleMarket)
	mux.HandleFunc("GET /features/meta", r.handleMeta)
	mux.HandleFunc("GET /features/sector/{name}", r.handleSector)
	mux.HandleFunc("GET /features/{isin}", r.handleStock)
	mux.HandleFunc("GET /features", r.handleAllFeatures)
	return mux
}

func (r *RESTServer) handleAllFeatures(w http.ResponseWriter, req *http.Request) {
	snap := r.engine.Snapshot()
	writeJSON(w, snap.Stocks)
}

func (r *RESTServer) handleStock(w http.ResponseWriter, req *http.Request) {
	isin := req.PathValue("isin")
	snap := r.engine.Snapshot()
	stock, ok := snap.Stocks[isin]
	if !ok {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	writeJSON(w, stock)
}

func (r *RESTServer) handleMarket(w http.ResponseWriter, req *http.Request) {
	snap := r.engine.Snapshot()
	writeJSON(w, snap.Market)
}

func (r *RESTServer) handleSector(w http.ResponseWriter, req *http.Request) {
	name := req.PathValue("name")
	snap := r.engine.Snapshot()
	sector, ok := snap.Sectors[name]
	if !ok {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	writeJSON(w, sector)
}

func (r *RESTServer) handleMeta(w http.ResponseWriter, req *http.Request) {
	writeJSON(w, map[string]interface{}{
		"version":  1,
		"features": []string{},
	})
}

func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
