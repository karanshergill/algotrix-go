# Phase 1: Add volume_spike_ratio + classified_volume_5m Features

**Goal:** Add 2 missing features to the Go Feature Engine that the screener port requires.

**Constraint:** DO NOT modify any existing feature behavior. These are purely additive changes.

---

## Task 1: Add Slot Volume Accumulator to StockState

**File:** `engine/features/state.go`

Add these fields to `StockState`:

```go
// === Slot Volume Accumulator ===
CurrentSlotVol  int64  // volume accumulated in the current 5-min slot
CurrentSlot     int    // current slot index (0 = 09:15, 1 = 09:20, ...)
CurrentSlotSet  bool   // true after first tick sets the slot
```

**File:** `engine/features/engine.go`

In `handleTick()`, BEFORE the existing volume delta logic, add slot volume tracking.
After the volume delta is computed (the `volumeDelta` variable already exists), add:

```go
// Slot volume accumulator — resets on slot boundary
if volumeDelta > 0 {
    currentSlot := timeToSlot(ev.TS)
    if !s.CurrentSlotSet || currentSlot != s.CurrentSlot {
        // New slot — reset accumulator
        s.CurrentSlot = currentSlot
        s.CurrentSlotVol = volumeDelta
        s.CurrentSlotSet = true
    } else {
        s.CurrentSlotVol += volumeDelta
    }
}
```

The `timeToSlot()` function already exists in the codebase — it computes `FLOOR((timestamp - 09:15) / 300)` as an int.

**Test:** `engine/features/state_test.go`
```go
func TestSlotVolumeAccumulator(t *testing.T) {
    // Test 1: First tick sets slot and volume
    // Test 2: Same slot accumulates
    // Test 3: New slot resets accumulator
}
```

---

## Task 2: Register volume_spike_ratio Feature

**File:** `engine/features/features_volume.go`

Add to `RegisterVolumeFeatures()`:

```go
r.Register(FeatureDef{
    Name: "volume_spike_ratio", Version: 1, Category: "volume",
    Trigger: TriggerTick,
    Ready: func(s *StockState, m *MarketState) bool {
        if !s.CurrentSlotSet {
            return false
        }
        b, ok := s.VolumeSlot[s.CurrentSlot]
        return ok && b.Mean >= 10000 && b.Samples >= 5
    },
    Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
        b := s.VolumeSlot[s.CurrentSlot]
        if s.CurrentSlotVol <= 0 {
            return 0
        }
        return float64(s.CurrentSlotVol) / b.Mean
    },
})
```

**Key details:**
- Uses `CurrentSlotVol` (slot accumulator) NOT `Volume5m.Sum()` (rolling window)
- `b.Mean` is the historical average volume for this exact 5-min slot
- Ready check: baseline must have Mean >= 10000 and >= 5 samples (matches v2 floor)
- Returns 0 when not ready (no spike for illiquid stocks)

**Test:** `engine/features/features_volume_test.go`
```go
func TestVolumeSpikeRatio(t *testing.T) {
    // Setup: stock with VolumeSlot baseline Mean=50000, Samples=10
    // Set CurrentSlotVol=100000, CurrentSlot=2
    // Expected: ratio = 2.0

    // Test low baseline (Mean < 10000): should not be ready
    // Test insufficient samples (< 5): should not be ready  
    // Test zero volume: should return 0
}
```

---

## Task 3: Register classified_volume_5m Feature

**File:** `engine/features/features_volume.go`

Add to `RegisterVolumeFeatures()`:

```go
r.Register(FeatureDef{
    Name: "classified_volume_5m", Version: 1, Category: "volume",
    Trigger: TriggerTick,
    Ready:   func(s *StockState, m *MarketState) bool { return true },
    Compute: func(s *StockState, m *MarketState, sec *SectorState) float64 {
        return float64(s.BuyVol5m.Sum() + s.SellVol5m.Sum())
    },
})
```

**Key details:**
- `BuyVol5m` and `SellVol5m` are existing `RollingSum` fields (300s window)
- This just exposes their sum as a feature — the screener checks `classified_volume_5m >= 5000`

**Test:**
```go
func TestClassifiedVolume5m(t *testing.T) {
    // Setup: stock with BuyVol5m=3000, SellVol5m=2500
    // Expected: 5500
    // Test empty: both zero → 0
}
```

---

## Task 4: Update NewDefaultRegistry Count

**File:** `engine/features/registry.go`

The `NewDefaultRegistry()` function comment says "17 features". Update to "19 features" since we're adding 2.

Also check if there's a feature count assertion in tests and update it.

---

## Task 5: Build and Test

```bash
cd /home/me/projects/algotrix-go
go build ./engine/...
go test ./engine/features/ -v -run "SlotVolume|VolumeSpikeRatio|ClassifiedVolume"
go test ./engine/features/ -v  # run all to ensure nothing broke
```

---

## Task 6: Commit

```bash
cd /home/me/projects/algotrix-go
git add engine/features/state.go engine/features/engine.go engine/features/features_volume.go engine/features/registry.go
git add engine/features/*_test.go
git commit -m "feat(features): add volume_spike_ratio and classified_volume_5m features

- Add slot volume accumulator to StockState (CurrentSlotVol, CurrentSlot)
- volume_spike_ratio: current slot volume / historical slot baseline mean
- classified_volume_5m: total buy+sell volume in 5-min rolling window
- Both features required for screener port from algotrix-v2"
```

When completely finished, run this command to notify me:
openclaw system event --text "Done: Added volume_spike_ratio and classified_volume_5m features to Go Feature Engine" --mode now
