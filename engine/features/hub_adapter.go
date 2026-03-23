package features

import "encoding/json"

// HubAdapter implements HubBroadcaster by serializing enriched messages
// and passing them to a generic broadcast function (e.g. feed.Hub.Broadcast).
type HubAdapter struct {
	broadcast func(msg []byte)
}

// NewHubAdapter creates a HubAdapter that sends serialized JSON to the
// provided broadcast function. The function is typically hub.Broadcast.
func NewHubAdapter(broadcast func(msg []byte)) *HubAdapter {
	return &HubAdapter{broadcast: broadcast}
}

// BroadcastTick sends an enriched tick message with features and quality.
func (h *HubAdapter) BroadcastTick(isin string, s *StockState, features map[string]float64, quality QualityFlags) {
	msg := map[string]interface{}{
		"type":   "tick",
		"isin":   isin,
		"symbol": s.Symbol,
		"ts":     s.LastTickTS.Unix(),
		"ltp":    s.LTP,
		"volume": s.CumulativeVolume,
		"features": features,
		"quality": map[string]interface{}{
			"partial":          quality.Partial,
			"baseline_missing": quality.BaselineMissing,
			"depth_stale_ms":   quality.DepthStaleMs,
			"tick_stale_ms":    quality.TickStaleMs,
		},
	}
	data, err := json.Marshal(msg)
	if err != nil {
		return
	}
	h.broadcast(data)
}

// BroadcastDepthFeatures sends a depth-triggered feature message.
func (h *HubAdapter) BroadcastDepthFeatures(isin string, s *StockState, features map[string]float64) {
	msg := map[string]interface{}{
		"type":     "depth_features",
		"isin":     isin,
		"symbol":   s.Symbol,
		"ts":       s.LastDepthTS.Unix(),
		"features": features,
	}
	data, err := json.Marshal(msg)
	if err != nil {
		return
	}
	h.broadcast(data)
}
