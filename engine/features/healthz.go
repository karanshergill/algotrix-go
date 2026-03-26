package features

import (
	"encoding/json"
	"net/http"
	"os"
	"runtime"
	"sync/atomic"
	"time"
)

var (
	engineStartTime time.Time
	lastTickTime    atomic.Value // stores time.Time
	tickRing        [60]int64   // per-second tick counts, ring buffer
	tickRingIdx     atomic.Int64
)

func init() {
	engineStartTime = time.Now()
	lastTickTime.Store(time.Time{})
}

// RecordTick should be called from the engine's tick handler.
func RecordTick() {
	lastTickTime.Store(time.Now())
	sec := time.Now().Unix() % 60
	atomic.AddInt64(&tickRing[sec], 1)
}

// ResetTickSlot clears the next second's slot so stale data from 60s ago
// is gone before new ticks land in it. Called from the 1s timer.
func ResetTickSlot() {
	next := (time.Now().Unix() + 1) % 60
	atomic.StoreInt64(&tickRing[next], 0)
}

func ticksLastMinute() int64 {
	var total int64
	for i := range tickRing {
		total += atomic.LoadInt64(&tickRing[i])
	}
	return total
}

func (r *RESTServer) handleHealthz(w http.ResponseWriter, req *http.Request) {
	var m runtime.MemStats
	runtime.ReadMemStats(&m)

	snap := r.engine.Snapshot()
	lt := lastTickTime.Load().(time.Time)
	var lastTickStr string
	if !lt.IsZero() {
		lastTickStr = lt.Format(time.RFC3339)
	}

	resp := map[string]interface{}{
		"status":           "running",
		"pid":              os.Getpid(),
		"uptime_seconds":   int(time.Since(engineStartTime).Seconds()),
		"stocks_registered": len(snap.Stocks),
		"features_active":  len(r.engine.registry.FeatureNames()),
		"last_tick_at":     lastTickStr,
		"ticks_last_minute": ticksLastMinute(),
		"memory_mb":        int(m.Alloc / 1024 / 1024),
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}
