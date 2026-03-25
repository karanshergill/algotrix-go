package features

import (
	"context"
	"sync/atomic"
	"time"
)

// ---------------------------------------------------------------------------
// Event types — fed into the engine via channels from the feed layer.
// ---------------------------------------------------------------------------

// TickEvent represents a single price/volume update from the feed.
type TickEvent struct {
	ISIN   string
	Symbol string
	LTP    float64
	Volume int64
	TS     time.Time
}

// DepthEvent represents a market depth snapshot from the feed.
type DepthEvent struct {
	ISIN string
	Bids []DepthLevel
	Asks []DepthLevel
	TS   time.Time
}

// DepthLevel is a single price level in the order book.
type DepthLevel struct {
	Price float64
	Qty   int
}

// ---------------------------------------------------------------------------
// HubBroadcaster — interface for broadcasting enriched data to consumers.
// Placeholder: real implementation comes in a later step.
// ---------------------------------------------------------------------------

// HubBroadcaster defines the interface for broadcasting engine output.
type HubBroadcaster interface {
	BroadcastTick(isin string, s *StockState, features map[string]float64, quality QualityFlags)
	BroadcastDepthFeatures(isin string, s *StockState, features map[string]float64)
}

// EngineConfig and DefaultEngineConfig are defined in config.go.

// ---------------------------------------------------------------------------
// FeatureEngine — the central orchestrator.
// All state mutations happen in the single-writer Run() goroutine.
// ---------------------------------------------------------------------------

// FeatureEngine owns all mutable state and processes events sequentially.
type FeatureEngine struct {
	stocks   map[string]*StockState
	sectors  map[string]*SectorState
	market   *MarketState
	session  *SessionManager
	registry *Registry

	guards      map[string]*FeedGuard
	guardConfig *GuardConfig

	tickCh  chan TickEvent
	depthCh chan DepthEvent

	hub HubBroadcaster // nil-safe — check before calling

	latestSnapshot atomic.Pointer[EngineSnapshot]

	// dirtyISINs and dirtyFeatures are ONLY written from Run() goroutine.
	// Both tick and depth events arrive via tickCh/depthCh channels and are
	// processed sequentially in the select loop — no mutex needed.
	dirtyISINs     map[string]bool
	dirtyFeatures  map[string]map[string]float64
	snapshotTicker *time.Ticker

	// Optional callbacks for testing (called after each event processed)
	onTick  func(isin string)
	onDepth func(isin string)
}

// NewFeatureEngine creates a FeatureEngine with the given config.
func NewFeatureEngine(config *EngineConfig) *FeatureEngine {
	if config == nil {
		config = DefaultEngineConfig()
	}

	stocks := make(map[string]*StockState)
	sectors := make(map[string]*SectorState)
	market := &MarketState{}

	fe := &FeatureEngine{
		stocks:        stocks,
		sectors:       sectors,
		market:        market,
		session:       NewSessionManager(stocks, market, sectors),
		registry:      NewDefaultRegistry(),
		guards:        make(map[string]*FeedGuard),
		guardConfig:   config.GuardConfig,
		tickCh:        make(chan TickEvent, config.TickBuffer),
		depthCh:       make(chan DepthEvent, config.DepthBuffer),
		dirtyISINs:    make(map[string]bool),
		dirtyFeatures: make(map[string]map[string]float64),
	}
	fe.latestSnapshot.Store(NewEngineSnapshot())
	return fe
}

// RegisterStock adds a StockState with initialized rolling windows.
func (e *FeatureEngine) RegisterStock(isin, symbol, sectorID string) {
	s := &StockState{
		ISIN:     isin,
		Symbol:   symbol,
		SectorID: sectorID,
		Volume1m:  NewRollingSum(60*time.Second, 4096),
		Volume5m:  NewRollingSum(300*time.Second, 16384),
		BuyVol5m:  NewRollingSum(300*time.Second, 16384),
		SellVol5m: NewRollingSum(300*time.Second, 16384),
		Updates1m: NewRollingSum(60*time.Second, 4096),
		High5m:    NewRollingExtreme(300*time.Second, true),
		Low5m:     NewRollingExtreme(300*time.Second, false),
	}
	e.stocks[isin] = s
	e.market.TotalStocks = len(e.stocks)
}

// RegisterSector adds a SectorState.
func (e *FeatureEngine) RegisterSector(name string, memberISINs []string) {
	e.sectors[name] = &SectorState{
		Name:        name,
		MemberISINs: memberISINs,
		TotalStocks: len(memberISINs),
	}
}

// TickChan returns the send-only tick channel.
func (e *FeatureEngine) TickChan() chan<- TickEvent { return e.tickCh }

// DepthChan returns the send-only depth channel.
func (e *FeatureEngine) DepthChan() chan<- DepthEvent { return e.depthCh }

// Session returns the session manager for external callers (e.g. session start/end).
func (e *FeatureEngine) Session() *SessionManager { return e.session }

// Market returns the market state (read-only access from outside the loop).
func (e *FeatureEngine) Market() *MarketState { return e.market }

// Stock returns a stock state by ISIN (nil if not registered).
func (e *FeatureEngine) Stock(isin string) *StockState { return e.stocks[isin] }

// Stocks returns the stocks map (read-only use).
func (e *FeatureEngine) Stocks() map[string]*StockState { return e.stocks }

// SetHub sets the hub broadcaster (nil-safe pattern — hub integration comes later).
func (e *FeatureEngine) SetHub(hub HubBroadcaster) { e.hub = hub }

// SetOnTick sets the callback invoked after each tick is processed.
func (e *FeatureEngine) SetOnTick(fn func(isin string)) { e.onTick = fn }

// SetOnDepth sets the callback invoked after each depth event is processed.
func (e *FeatureEngine) SetOnDepth(fn func(isin string)) { e.onDepth = fn }

// ---------------------------------------------------------------------------
// Run — the single-writer event loop. ONLY this goroutine mutates state.
// ---------------------------------------------------------------------------

// Run processes events until ctx is cancelled.
func (e *FeatureEngine) Run(ctx context.Context) {
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()

	e.snapshotTicker = time.NewTicker(250 * time.Millisecond)
	defer e.snapshotTicker.Stop()

	for {
		select {
		case ev := <-e.tickCh:
			e.handleTick(ev)
		case ev := <-e.depthCh:
			e.handleDepth(ev)
		case <-e.snapshotTicker.C:
			e.rebuildSnapshot()
		case <-ticker.C:
			e.handleTimer()
		case <-ctx.Done():
			return
		}
	}
}

// ---------------------------------------------------------------------------
// handleTick — Section 1.9 from the plan.
// ---------------------------------------------------------------------------

func (e *FeatureEngine) handleTick(ev TickEvent) {
	RecordTick()
	s := e.stocks[ev.ISIN]
	if s == nil {
		return
	}

	// Lazy guard creation
	guard, ok := e.guards[ev.ISIN]
	if !ok {
		guard = NewFeedGuard(e.guardConfig)
		e.guards[ev.ISIN] = guard
	}

	// Feed guard
	if ok, _ := guard.ValidateTick(ev.ISIN, ev.LTP, ev.Volume, ev.TS); !ok {
		return
	}

	// Session gate
	if !e.session.IsAccepting() {
		return
	}

	// Volume delta
	volumeDelta := ev.Volume - s.CumulativeVolume
	if volumeDelta <= 0 {
		// Price-only update: still update LTP/OHLCV but skip volume/classification
		s.LTP = ev.LTP
		s.LastTickTS = ev.TS
		if ev.LTP > s.DayHigh {
			s.DayHigh = ev.LTP
		}
		if ev.LTP < s.DayLow || s.DayLow == 0 {
			s.DayLow = ev.LTP
		}
	} else {
		s.LTP = ev.LTP
		s.LastTickTS = ev.TS
		if s.DayOpen == 0 {
			s.DayOpen = ev.LTP
		}
		if ev.LTP > s.DayHigh {
			s.DayHigh = ev.LTP
		}
		if ev.LTP < s.DayLow || s.DayLow == 0 {
			s.DayLow = ev.LTP
		}

		s.CumulativeVolume = ev.Volume
		s.CumulativeTurnover += ev.LTP * float64(volumeDelta)
		s.UpdateCount++

		s.ClassifyTick(ev.LTP, volumeDelta, ev.TS)

		s.Volume1m.Add(ev.TS, volumeDelta)
		s.Volume5m.Add(ev.TS, volumeDelta)
		s.Updates1m.Add(ev.TS, 1)
		s.High5m.Add(ev.TS, ev.LTP)
		s.Low5m.Add(ev.TS, ev.LTP)

		// Slot volume accumulator — resets on slot boundary
		currentSlot := timeToSlot(ev.TS)
		if !s.CurrentSlotSet || currentSlot != s.CurrentSlot {
			s.CurrentSlot = currentSlot
			s.CurrentSlotVol = volumeDelta
			s.CurrentSlotSet = true
		} else {
			s.CurrentSlotVol += volumeDelta
		}
	}

	// Compute VWAP
	vwap := 0.0
	if s.CumulativeVolume > 0 {
		vwap = s.CumulativeTurnover / float64(s.CumulativeVolume)
	}

	// Delta-update market + sector
	// Both UpdateFromStock methods read from and write to shared prev* fields
	// on StockState. Save prev before market update, restore for sector update.
	saved := savePrev(s)
	e.market.UpdateFromStock(s, vwap)
	restorePrev(s, saved)
	if sec, ok := e.sectors[s.SectorID]; ok {
		sec.UpdateFromStock(s, vwap)
	}

	// Compute tick-triggered features
	sec, _ := e.sectors[s.SectorID]
	fv := e.registry.ComputeTriggered(s, e.market, sec, TriggerTick)
	featureMap := e.registry.ToMap(fv)
	e.registry.ReleaseVector(fv)

	// Update immutable snapshot
	e.updateSnapshotWithFeatures(s, featureMap)

	// Hub broadcast (nil-safe)
	if e.hub != nil {
		quality := ComputeQuality(s, time.Now())
		e.hub.BroadcastTick(ev.ISIN, s, featureMap, quality)
	}

	// Test callback
	if e.onTick != nil {
		e.onTick(ev.ISIN)
	}
}

// ---------------------------------------------------------------------------
// handleDepth — Section 1.9 from the plan.
// ---------------------------------------------------------------------------

func (e *FeatureEngine) handleDepth(ev DepthEvent) {
	s := e.stocks[ev.ISIN]
	if s == nil {
		return
	}

	// Session gate
	if !e.session.IsAccepting() {
		return
	}

	s.TotalBidQty = 0
	s.TotalAskQty = 0
	for i := 0; i < 5 && i < len(ev.Bids); i++ {
		s.BidPrices[i] = ev.Bids[i].Price
		s.BidQtys[i] = int64(ev.Bids[i].Qty)
		s.TotalBidQty += s.BidQtys[i]
	}
	for i := 0; i < 5 && i < len(ev.Asks); i++ {
		s.AskPrices[i] = ev.Asks[i].Price
		s.AskQtys[i] = int64(ev.Asks[i].Qty)
		s.TotalAskQty += s.AskQtys[i]
	}
	s.HasDepth = true
	s.LastDepthTS = ev.TS

	// Compute depth-triggered features
	sec, _ := e.sectors[s.SectorID]
	fv := e.registry.ComputeTriggered(s, e.market, sec, TriggerDepth)
	featureMap := e.registry.ToMap(fv)
	e.registry.ReleaseVector(fv)

	// Update immutable snapshot
	e.updateSnapshotWithFeatures(s, featureMap)

	// Hub broadcast (nil-safe)
	if e.hub != nil {
		e.hub.BroadcastDepthFeatures(ev.ISIN, s, featureMap)
	}

	// Test callback
	if e.onDepth != nil {
		e.onDepth(ev.ISIN)
	}
}

// prevSnapshot holds saved prev* fields for delta-tracking coordination.
type prevSnapshot struct {
	registered   bool
	wasUp        bool
	wasDown      bool
	wasAboveVWAP bool
	buyVol       int64
	sellVol      int64
	volume       int64
	turnover     float64
}

func savePrev(s *StockState) prevSnapshot {
	return prevSnapshot{
		registered:   s.prevRegistered,
		wasUp:        s.prevWasUp,
		wasDown:      s.prevWasDown,
		wasAboveVWAP: s.prevWasAboveVWAP,
		buyVol:       s.prevBuyVol,
		sellVol:      s.prevSellVol,
		volume:       s.prevVolume,
		turnover:     s.prevTurnover,
	}
}

func restorePrev(s *StockState, snap prevSnapshot) {
	s.prevRegistered = snap.registered
	s.prevWasUp = snap.wasUp
	s.prevWasDown = snap.wasDown
	s.prevWasAboveVWAP = snap.wasAboveVWAP
	s.prevBuyVol = snap.buyVol
	s.prevSellVol = snap.sellVol
	s.prevVolume = snap.volume
	s.prevTurnover = snap.turnover
}

// Snapshot returns the latest immutable snapshot (safe for concurrent reads).
func (e *FeatureEngine) Snapshot() *EngineSnapshot {
	return e.latestSnapshot.Load()
}

// updateSnapshotWithFeatures marks the stock as dirty and saves its features.
// The actual snapshot rebuild happens on the 250ms timer in rebuildSnapshot.
func (e *FeatureEngine) updateSnapshotWithFeatures(s *StockState, features map[string]float64) {
	e.dirtyISINs[s.ISIN] = true
	// Store only the delta (newly computed features). No cloning of previous snapshot.
	// rebuildSnapshot merges deltas with the previous snapshot's features.
	existing, ok := e.dirtyFeatures[s.ISIN]
	if !ok || existing == nil {
		e.dirtyFeatures[s.ISIN] = features
		return
	}
	// Already have dirty features this cycle — merge in new ones
	for k, v := range features {
		existing[k] = v
	}
}

// rebuildSnapshot builds a new EngineSnapshot from all dirty stocks.
// Called every 250ms from the event loop — single-threaded, no mutex needed.
func (e *FeatureEngine) rebuildSnapshot() {
	if len(e.dirtyISINs) == 0 {
		return
	}

	snap := e.latestSnapshot.Load()

	// Build new stock map — copy all existing, update dirty ones
	newStocks := make(map[string]StockSnapshot, len(snap.Stocks))
	for k, v := range snap.Stocks {
		newStocks[k] = v
	}

	for isin := range e.dirtyISINs {
		s := e.stocks[isin]
		if s == nil {
			continue
		}
		// Merge deltas with previous snapshot features
		delta := e.dirtyFeatures[isin]
		var merged map[string]float64
		if prev, exists := snap.Stocks[isin]; exists && prev.Features != nil {
			merged = make(map[string]float64, len(prev.Features)+len(delta))
			for k, v := range prev.Features {
				merged[k] = v
			}
			for k, v := range delta {
				merged[k] = v
			}
		} else {
			merged = delta
		}
		newStocks[isin] = StockSnapshot{
			ISIN:     s.ISIN,
			Symbol:   s.Symbol,
			LTP:      s.LTP,
			Features: merged,
			Quality:  ComputeQuality(s, time.Now()),
		}
	}

	// Build sectors
	sectors := make(map[string]SectorSnapshot, len(e.sectors))
	for id, sec := range e.sectors {
		sectors[id] = SectorSnapshotFrom(sec)
	}

	newSnap := &EngineSnapshot{
		Stocks:  newStocks,
		Market:  MarketSnapshotFrom(e.market),
		Sectors: sectors,
		TS:      time.Now(),
	}
	e.latestSnapshot.Store(newSnap)

	// Clear dirty set
	e.dirtyISINs = make(map[string]bool)
	e.dirtyFeatures = make(map[string]map[string]float64)
}

// handleTimer is a placeholder for timer-triggered features.
func (e *FeatureEngine) handleTimer() {
	// Placeholder for future timer-triggered features (e.g. periodic snapshots)
}

// ---------------------------------------------------------------------------
// ClassifyTick — Section 1.8: tick rule classification.
// ---------------------------------------------------------------------------

// ClassifyTick uses the tick rule to classify volume as buy or sell.
// price > lastLTP → buy, price < lastLTP → sell, equal → use last direction.
func (s *StockState) ClassifyTick(price float64, volumeDelta int64, ts time.Time) {
	if price > s.LastLTP {
		s.LastDirection = 1
	} else if price < s.LastLTP {
		s.LastDirection = -1
	}
	// If equal, LastDirection stays unchanged (tick rule)

	if s.LastDirection >= 0 {
		s.CumulativeBuyVol += volumeDelta
		s.BuyVol5m.Add(ts, volumeDelta)
	} else {
		s.CumulativeSellVol += volumeDelta
		s.SellVol5m.Add(ts, volumeDelta)
	}
	s.LastLTP = price
}
