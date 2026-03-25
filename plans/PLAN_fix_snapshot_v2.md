# Fix: Snapshot Memory — Timer-Driven Approach (v2)

**Problem:** Even with shallow copy, creating a new 691-entry map on every tick allocates too much (889MB in 40s). We need ZERO allocation on the tick hot path.

**Fix:** Timer-driven snapshot rebuilds. Per-tick: only update StockState (already happening) + mark dirty. Every 250ms: build ONE new snapshot from all states.

---

## Task 1: Add Dirty Tracking + Timer to FeatureEngine

**File:** `engine/features/engine.go`

1. Add fields to `FeatureEngine`:
```go
dirtyISINs    map[string]bool       // ISINs updated since last snapshot
dirtyFeatures map[string]map[string]float64  // ISIN → computed features (saved for snapshot build)
snapshotTicker *time.Ticker
```

2. Initialize in constructor/startup:
```go
dirtyISINs:    make(map[string]bool),
dirtyFeatures: make(map[string]map[string]float64),
```

3. Replace `updateSnapshotWithFeatures` — just save the data, don't build snapshot:
```go
func (e *FeatureEngine) updateSnapshotWithFeatures(s *StockState, features map[string]float64) {
	e.dirtyISINs[s.ISIN] = true
	e.dirtyFeatures[s.ISIN] = features
}
```

4. Add a `rebuildSnapshot` method that runs on a timer (every 250ms):
```go
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
		features := e.dirtyFeatures[isin]
		newStocks[isin] = StockSnapshot{
			ISIN:     s.ISIN,
			Symbol:   s.Symbol,
			LTP:      s.LTP,
			Features: features,
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
```

5. Start the ticker in the event loop (in `Run()` or wherever the event loop starts). Add a case to the select:
```go
// In the event loop select:
case <-e.snapshotTicker.C:
    e.rebuildSnapshot()
```

Initialize the ticker:
```go
e.snapshotTicker = time.NewTicker(250 * time.Millisecond)
```

**IMPORTANT:** The event loop is single-threaded. Both `updateSnapshotWithFeatures` and `rebuildSnapshot` run on the same goroutine (event loop select). No mutex needed for dirtyISINs/dirtyFeatures.

## Task 2: Remove sectorUpdateCounter

The `sectorUpdateCounter` field added in the previous fix is no longer needed. Remove it.

## Task 3: Build and Deploy

```bash
cd /home/me/projects/algotrix-go/engine
go build -o algotrix .
```

Then test:
```bash
pm2 delete go-feed && pm2 start ecosystem.config.cjs --only go-feed
sleep 30
pm2 list | grep go-feed  # Memory should stay under 200MB
sleep 30
pm2 list | grep go-feed  # Should be stable, not growing
```

## Task 4: Commit

```bash
git add engine/features/engine.go
git commit -m "perf(snapshot): timer-driven snapshot rebuild every 250ms

- Per-tick: only mark ISIN as dirty + save features (zero allocation)
- Every 250ms: rebuild snapshot with all dirty stocks in one batch
- Fixes memory explosion: was creating 691-entry map per tick"
```

When completely finished, run this command to notify me:
openclaw system event --text "Done: Timer-driven snapshot fix applied" --mode now
