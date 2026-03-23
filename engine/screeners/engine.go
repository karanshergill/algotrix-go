package screeners

import (
	"log"
	"time"

	"github.com/karanshergill/algotrix-go/features"
)

// Engine manages all screeners and routes ticks to them.
type Engine struct {
	screeners      []Screener
	prevLTP        map[string]float64 // "screenerName:ISIN" -> last LTP
	triggeredToday map[string]bool    // "screenerName:ISIN" -> already fired
	sessionDate    string             // "2006-01-02" for day rollover detection
	db             *SignalDB          // nil = no persistence (testing)
}

// NewEngine creates a screener engine.
func NewEngine(screeners []Screener, db *SignalDB) *Engine {
	return &Engine{
		screeners:      screeners,
		prevLTP:        make(map[string]float64),
		triggeredToday: make(map[string]bool),
		db:             db,
	}
}

// ProcessTick evaluates all screeners for one stock tick.
// Call this from the feature engine's onTick callback.
func (e *Engine) ProcessTick(isin string, stockSnap *features.StockSnapshot, marketSnap *features.MarketSnapshot) []*Signal {
	now := time.Now()
	ist := now.In(time.FixedZone("IST", 5*3600+30*60))

	// Day rollover check
	today := ist.Format("2006-01-02")
	if e.sessionDate != "" && today != e.sessionDate {
		e.resetDay()
	}
	e.sessionDate = today

	// Market hours gate: 09:15 - 15:30 IST
	hour, min := ist.Hour(), ist.Minute()
	marketMinute := hour*60 + min
	if marketMinute < 9*60+15 || marketMinute > 15*60+30 {
		return nil
	}

	var signals []*Signal

	mctx := MarketContext{
		NiftyLTP:       marketSnap.NiftyLTP,
		NiftyPrevClose: marketSnap.NiftyPrevClose,
	}

	for _, scr := range e.screeners {
		key := scr.Name() + ":" + isin

		// Dedup: skip if already signaled today
		if e.triggeredToday[key] {
			continue
		}

		prevLTP := e.prevLTP[key]
		e.prevLTP[key] = stockSnap.LTP

		ctx := &TickContext{
			ISIN:     isin,
			Symbol:   stockSnap.Symbol,
			LTP:      stockSnap.LTP,
			Features: stockSnap.Features,
			Market:   mctx,
			TickTime: ist,
			PrevLTP:  prevLTP,
		}

		sig := scr.Evaluate(ctx)
		if sig != nil {
			sig.TriggeredAt = ist
			sig.ISIN = isin
			sig.Symbol = stockSnap.Symbol
			sig.LTP = stockSnap.LTP
			sig.TriggerPrice = stockSnap.LTP

			e.triggeredToday[key] = true
			signals = append(signals, sig)

			log.Printf("[screener] %s SIGNAL: %s %s @ %.2f (%s)",
				sig.ScreenerName, sig.SignalType, sig.Symbol, sig.LTP, sig.ISIN)

			// Persist to DB
			if e.db != nil {
				if err := e.db.PersistSignal(sig, today); err != nil {
					log.Printf("[screener] DB persist error: %v", err)
				}
			}
		}
	}

	return signals
}

func (e *Engine) resetDay() {
	log.Println("[screener] Day rollover — resetting all screeners")
	e.prevLTP = make(map[string]float64)
	e.triggeredToday = make(map[string]bool)
	for _, scr := range e.screeners {
		scr.Reset()
	}
}
