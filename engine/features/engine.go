package features

import (
	"context"
	"log"
	"sync/atomic"
	"time"
)

// ---------------------------------------------------------------------------
// Event types — fed into the engine via channels from the feed layer.
// ---------------------------------------------------------------------------

// TickEvent represents a single price/volume update from the feed.
type TickEvent struct {
	ISIN           string
	Symbol         string
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
	dirtyISINs    map[string]bool
	dirtyFeatures map[string]map[string]float64

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
		Volume1m:         NewRollingSum(60*time.Second, 8192),
		Volume5m:         NewRollingSum(300*time.Second, 32768),
		BuyVol5m:         NewRollingSum(300*time.Second, 32768),
		SellVol5m:        NewRollingSum(300*time.Second, 32768),
		Updates1m:        NewRollingSum(60*time.Second, 8192),
		High5m:           NewRollingExtreme(300*time.Second, true),
		Low5m:            NewRollingExtreme(300*time.Second, false),
		BookImbalance60s: NewRollingAvg(60*time.Second, 1024),
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

	for {
		select {
		case ev := <-e.tickCh:
			e.handleTick(ev)
		case ev := <-e.depthCh:
			e.handleDepth(ev)
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

	// sf carries more frequent top-of-book quantities than dp, so keep them fresh
	// on every tick even when there is no new traded volume.
	s.TotalBidQty = ev.TotBuyQty
	s.TotalAskQty = ev.TotSellQty
	if ev.BidPrice > 0 {
		s.BidPrices[0] = ev.BidPrice
		s.BidQtys[0] = ev.BidSize
	}
	if ev.AskPrice > 0 {
		s.AskPrices[0] = ev.AskPrice
		s.AskQtys[0] = ev.AskSize
	}
	if ev.AvgTradePrice > 0 {
		s.ExchVWAP = ev.AvgTradePrice
	}
	s.LastTradedQty = ev.LastTradedQty
	if ev.YearHigh > 0 {
		s.YearHigh = ev.YearHigh
	}
	if ev.YearLow > 0 {
		s.YearLow = ev.YearLow
	}
	if ev.LowerCircuit > 0 {
		s.LowerCircuit = ev.LowerCircuit
	}
	if ev.UpperCircuit > 0 {
		s.UpperCircuit = ev.UpperCircuit
	}

	// Volume delta — detect reconnect resets where the feed restarts from a
	// lower cumulative value. Without this, volumeDelta stays negative and
	// all volume tracking (rolling windows, classification, slot accumulators)
	// is silently dead until the new feed catches up to the stale cumulative.
	volumeDelta := ev.Volume - s.CumulativeVolume
	if ev.Volume > 0 && ev.Volume < s.CumulativeVolume {
		// Volume reset (reconnect): rebase to the new feed's value.
		// Treat the new ev.Volume as a fresh start — no delta for this tick.
		log.Printf("[FeatureEngine] %s volume reset: %d → %d (rebasing)", ev.ISIN, s.CumulativeVolume, ev.Volume)
		s.CumulativeVolume = ev.Volume
		volumeDelta = 0
	}
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

	// VWAP: prefer exchange-provided (v2 parity), fall back to computed
	vwap := s.ExchVWAP
	if vwap == 0 && s.CumulativeVolume > 0 {
		vwap = s.CumulativeTurnover / float64(s.CumulativeVolume)
	}

	// Delta-update market + sector
	// Both UpdateFromStock methods read from and write to shared prev* fields
	// on StockState. Save prev before market update, restore for sector update.
	// restorePrev must only run when sector update follows — otherwise market's
	// prev* save is the correct final state.
	if sec, ok := e.sectors[s.SectorID]; ok {
		saved := savePrev(s)
		e.market.UpdateFromStock(s, vwap)
		restorePrev(s, saved)
		sec.UpdateFromStock(s, vwap)
	} else {
		e.market.UpdateFromStock(s, vwap)
	}

	// Compute tick-triggered features
	sec, _ := e.sectors[s.SectorID]
	fv := e.registry.ComputeTriggered(s, e.market, sec, TriggerTick)
	featureMap := e.registry.ToMap(fv)
	e.registry.ReleaseVector(fv)

	// Update and rebuild immutable snapshot synchronously
	e.updateSnapshotWithFeatures(s, featureMap)
	e.rebuildSnapshot()

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
	s.BidPrices = [5]float64{}
	s.BidQtys = [5]int64{}
	s.AskPrices = [5]float64{}
	s.AskQtys = [5]int64{}
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

	// Update book imbalance rolling average (v2 parity: 60s window of total qty ratios)
	totalQty := s.TotalBidQty + s.TotalAskQty
	if totalQty > 0 && s.BookImbalance60s != nil {
		ratio := float64(s.TotalBidQty) / float64(totalQty)
		s.BookImbalance60s.Add(ev.TS, ratio)
	}

	// Compute depth-triggered features
	sec, _ := e.sectors[s.SectorID]
	fv := e.registry.ComputeTriggered(s, e.market, sec, TriggerDepth)
	featureMap := e.registry.ToMap(fv)
	e.registry.ReleaseVector(fv)

	// Update and rebuild immutable snapshot synchronously
	e.updateSnapshotWithFeatures(s, featureMap)
	e.rebuildSnapshot()

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

// handleTimer runs on the 1s ticker — resets the tick-rate ring slot.
func (e *FeatureEngine) handleTimer() {
	ResetTickSlot()
}

// ---------------------------------------------------------------------------
// ClassifyTick — Quote Rule classification (v2 parity).
// ---------------------------------------------------------------------------

// ClassifyTick classifies volume as buy or sell using the Quote Rule (v2 parity).
//
// When depth is available (BidPrices[0] > 0 && AskPrices[0] > 0):
//
//	Quote Rule (matches v2 VolumeDirectionIndicator._classify):
//	1. ltp >= ask  → BUY  (buyer lifted the ask)
//	2. ltp <= bid  → SELL (seller hit the bid)
//	3. ltp > mid   → BUY
//	4. ltp < mid   → SELL
//	5. ltp == mid  → use TotalBidQty > TotalAskQty (book pressure tiebreak)
//
// When depth is unavailable (no bid/ask yet):
//
//	Tick Rule fallback (same as previous behaviour):
//	price > lastLTP → buy, price < lastLTP → sell, equal → repeat last direction
//
// Unclassifiable ticks (direction unknown at start, no depth, no prior direction)
// are skipped rather than defaulting to buy.
func (s *StockState) ClassifyTick(price float64, volumeDelta int64, ts time.Time) {
	bid := s.BidPrices[0]
	ask := s.AskPrices[0]

	var direction int8 // 0 = unknown, 1 = buy, -1 = sell

	if bid > 0 && ask > 0 && ask > bid {
		// Quote Rule — depth is available and book is not crossed
		if price >= ask {
			direction = 1
		} else if price <= bid {
			direction = -1
		} else {
			mid := (bid + ask) / 2.0
			if price > mid {
				direction = 1
			} else if price < mid {
				direction = -1
			} else {
				// Exactly at midpoint — v2 parity:
				// buy if total bid qty > total ask qty, else sell.
				if s.TotalBidQty > s.TotalAskQty {
					direction = 1
				} else {
					direction = -1
				}
			}
		}
		s.LastDirection = direction
	} else {
		// Tick Rule fallback (no depth available yet)
		if price > s.LastLTP {
			direction = 1
			s.LastDirection = 1
		} else if price < s.LastLTP {
			direction = -1
			s.LastDirection = -1
		} else {
			direction = s.LastDirection // repeat last known direction
		}
	}

	// Skip unclassifiable tick (no depth, no prior direction, no price change)
	if direction == 0 {
		s.LastLTP = price
		return
	}

	if direction > 0 {
		s.CumulativeBuyVol += volumeDelta
		s.BuyVol5m.Add(ts, volumeDelta)
	} else {
		s.CumulativeSellVol += volumeDelta
		s.SellVol5m.Add(ts, volumeDelta)
	}
	s.LastLTP = price
}
