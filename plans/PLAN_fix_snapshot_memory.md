# Fix: Snapshot Memory Explosion

**Problem:** `updateSnapshotWithFeatures()` calls `snap.Clone()` on every tick. At 691 stocks × ~1000 ticks/sec, this deep-copies the entire stock map + all feature maps thousands of times per second, causing 1.2GB memory usage in 25 seconds.

**Fix:** Replace per-tick full clone with in-place single-stock update + periodic full snapshot rebuild.

---

## Task 1: Replace Clone-Per-Tick with Single-Stock Update

**File:** `engine/features/engine.go`

Replace `updateSnapshotWithFeatures`:

```go
func (e *FeatureEngine) updateSnapshotWithFeatures(s *StockState, features map[string]float64) {
	// Build the new StockSnapshot for just this one stock
	stockSnap := StockSnapshot{
		ISIN:     s.ISIN,
		Symbol:   s.Symbol,
		LTP:      s.LTP,
		Features: features,  // already a new map from computeFeatures
		Quality:  ComputeQuality(s, time.Now()),
	}

	// Load current snapshot, create a shallow copy with ONE stock replaced
	snap := e.latestSnapshot.Load()
	newStocks := make(map[string]StockSnapshot, len(snap.Stocks))
	for k, v := range snap.Stocks {
		newStocks[k] = v  // shallow copy — no deep clone of feature maps
	}
	newStocks[s.ISIN] = stockSnap

	newSnap := &EngineSnapshot{
		Stocks:  newStocks,
		Market:  MarketSnapshotFrom(e.market),
		Sectors: snap.Sectors, // reuse sectors — update periodically
		TS:      time.Now(),
	}
	e.latestSnapshot.Store(newSnap)
}
```

**Why this works:**
- Old approach: deep-clone ALL 691 stocks + ALL feature maps on every tick
- New approach: shallow-copy the stock map (cheap pointer copies) + replace ONE stock entry
- Feature maps from previous ticks are immutable (never mutated after store) so shallow copy is safe
- Market snapshot still updates per tick (it's a small struct)
- Sector snapshots reuse the previous pointer (update them periodically or on sector ticks)

## Task 2: Update Sectors Periodically Instead of Per-Tick

The sector rebuild (`for id, sec := range e.sectors`) was also happening per tick. Move it to a timer or every Nth tick:

Add a counter to FeatureEngine:
```go
sectorUpdateCounter int
```

In `updateSnapshotWithFeatures`, only rebuild sectors every 100 ticks:
```go
sectors := snap.Sectors
e.sectorUpdateCounter++
if e.sectorUpdateCounter >= 100 {
    e.sectorUpdateCounter = 0
    sectors = make(map[string]SectorSnapshot, len(e.sectors))
    for id, sec := range e.sectors {
        sectors[id] = SectorSnapshotFrom(sec)
    }
}
```

## Task 3: Build, Test, Deploy

```bash
cd /home/me/projects/algotrix-go/engine
go build -o algotrix .
go test ./features/ -v -run Snapshot
```

Then start go-feed and monitor memory:
```bash
pm2 delete go-feed && pm2 start ecosystem.config.cjs --only go-feed
sleep 30
pm2 list | grep go-feed  # check memory stays under 200MB
```

## Task 4: Commit

```bash
git add engine/features/engine.go
git commit -m "perf(snapshot): replace per-tick full clone with shallow copy

- updateSnapshotWithFeatures now shallow-copies stock map instead of deep-cloning
- Only the updated stock's snapshot is replaced
- Sectors update every 100 ticks instead of every tick
- Fixes memory explosion (1.2GB in 25s → should stay under 200MB)"
```

When completely finished, run this command to notify me:
openclaw system event --text "Done: Snapshot memory fix applied — shallow copy replaces per-tick clone" --mode now
