package features

import (
	"log"
	"time"
)

// ---------------------------------------------------------------------------
// SessionState represents the current phase of the trading session.
// ---------------------------------------------------------------------------

type SessionState int

const (
	SessionPreOpen SessionState = iota // before market open
	SessionOpen                        // active trading
	SessionClosed                      // after market close
)

// ---------------------------------------------------------------------------
// SessionManager owns session lifecycle: start, end, and state resets.
// It holds references to shared state (not owned).
// ---------------------------------------------------------------------------

type SessionManager struct {
	state       SessionState
	currentDate time.Time
	stocks      map[string]*StockState
	market      *MarketState
	sectors     map[string]*SectorState
}

// NewSessionManager creates a SessionManager with references to shared state.
func NewSessionManager(stocks map[string]*StockState, market *MarketState, sectors map[string]*SectorState) *SessionManager {
	return &SessionManager{
		state:   SessionPreOpen,
		stocks:  stocks,
		market:  market,
		sectors: sectors,
	}
}

// State returns the current session state.
func (sm *SessionManager) State() SessionState {
	return sm.state
}

// IsAccepting returns true only when the session is open.
func (sm *SessionManager) IsAccepting() bool {
	return sm.state == SessionOpen
}

// SessionStart resets all intraday state, preserves baselines, and transitions to SessionOpen.
func (sm *SessionManager) SessionStart(date time.Time) {
	sm.currentDate = date

	for _, s := range sm.stocks {
		// Reset tick-updated primitives
		s.LTP = 0
		s.DayOpen = 0
		s.DayHigh = 0
		s.DayLow = 0
		s.LastTickTS = time.Time{}
		s.CumulativeVolume = 0
		s.CumulativeTurnover = 0
		s.CumulativeBuyVol = 0
		s.CumulativeSellVol = 0
		s.UpdateCount = 0
		s.LastDirection = 0
		s.LastLTP = 0

		// Reset depth primitives
		s.BidPrices = [5]float64{}
		s.BidQtys = [5]int64{}
		s.AskPrices = [5]float64{}
		s.AskQtys = [5]int64{}
		s.TotalBidQty = 0
		s.TotalAskQty = 0
		s.HasDepth = false
		s.LastDepthTS = time.Time{}

		// Reset rolling windows (nil-safe)
		if s.Volume1m != nil {
			s.Volume1m.Reset()
		}
		if s.Volume5m != nil {
			s.Volume5m.Reset()
		}
		if s.BuyVol5m != nil {
			s.BuyVol5m.Reset()
		}
		if s.SellVol5m != nil {
			s.SellVol5m.Reset()
		}
		if s.Updates1m != nil {
			s.Updates1m.Reset()
		}
		if s.High5m != nil {
			s.High5m.Reset()
		}
		if s.Low5m != nil {
			s.Low5m.Reset()
		}

		// Reset delta tracking
		s.prevRegistered = false
		s.prevWasUp = false
		s.prevWasDown = false
		s.prevWasAboveVWAP = false
		s.prevBuyVol = 0
		s.prevSellVol = 0
		s.prevVolume = 0
		s.prevTurnover = 0

		// Preserved: PrevClose, ATR14d, AvgDailyVolume, VolumeSlot, SectorID, ISIN, Symbol
	}

	// Reset market state — preserve only TotalStocks
	*sm.market = MarketState{TotalStocks: len(sm.stocks)}

	// Reset sector state — preserve Name, MemberISINs, TotalStocks
	for _, sec := range sm.sectors {
		*sec = SectorState{
			Name:        sec.Name,
			MemberISINs: sec.MemberISINs,
			TotalStocks: len(sec.MemberISINs),
		}
	}

	sm.state = SessionOpen
	log.Printf("[Session] Started for %s — %d stocks, %d sectors", date.Format("2006-01-02"), len(sm.stocks), len(sm.sectors))
}

// SessionEnd transitions to SessionClosed and logs final market state.
func (sm *SessionManager) SessionEnd() {
	sm.state = SessionClosed
	log.Printf("[Session] Ended — final market: %d up, %d down, %d flat",
		sm.market.StocksUp, sm.market.StocksDown, sm.market.StocksFlat)
}
