package features

import (
	"testing"
	"time"
)

// newTestStock creates a StockState with all rolling windows initialized.
func newTestStock() *StockState {
	return &StockState{
		Volume1m:  NewRollingSum(60*time.Second, 4096),
		Volume5m:  NewRollingSum(300*time.Second, 16384),
		BuyVol5m:  NewRollingSum(300*time.Second, 16384),
		SellVol5m: NewRollingSum(300*time.Second, 16384),
		Updates1m: NewRollingSum(60*time.Second, 4096),
		High5m:    NewRollingExtreme(300*time.Second, true),
		Low5m:     NewRollingExtreme(300*time.Second, false),
	}
}

func TestVolumeSpikeRatio(t *testing.T) {
	r := NewDefaultRegistry()
	m := &MarketState{}

	// Setup: stock with VolumeSlot baseline Mean=50000, Samples=10
	s := newTestStock()
	s.CurrentSlotVol = 100000
	s.CurrentSlot = 2
	s.CurrentSlotSet = true
	s.VolumeSlot = map[int]VolumeSlotBaseline{
		2: {Mean: 50000, StdDev: 5000, Samples: 10},
	}

	fv := r.ComputeTriggered(s, m, nil, TriggerTick)
	featureMap := r.ToMap(fv)
	r.ReleaseVector(fv)

	// Expected: ratio = 100000 / 50000 = 2.0
	if v, ok := featureMap["volume_spike_ratio"]; !ok {
		t.Error("volume_spike_ratio not in feature map")
	} else if v != 2.0 {
		t.Errorf("volume_spike_ratio = %f, want 2.0", v)
	}

	// Test low baseline (Mean < 10000): should not be ready
	s2 := newTestStock()
	s2.CurrentSlotVol = 5000
	s2.CurrentSlot = 3
	s2.CurrentSlotSet = true
	s2.VolumeSlot = map[int]VolumeSlotBaseline{
		3: {Mean: 9000, StdDev: 1000, Samples: 10},
	}
	fv2 := r.ComputeTriggered(s2, m, nil, TriggerTick)
	featureMap2 := r.ToMap(fv2)
	r.ReleaseVector(fv2)

	if _, ok := featureMap2["volume_spike_ratio"]; ok {
		t.Error("volume_spike_ratio should not be ready when Mean < 10000")
	}

	// Test insufficient samples (< 5): should not be ready
	s3 := newTestStock()
	s3.CurrentSlotVol = 20000
	s3.CurrentSlot = 4
	s3.CurrentSlotSet = true
	s3.VolumeSlot = map[int]VolumeSlotBaseline{
		4: {Mean: 50000, StdDev: 5000, Samples: 3},
	}
	fv3 := r.ComputeTriggered(s3, m, nil, TriggerTick)
	featureMap3 := r.ToMap(fv3)
	r.ReleaseVector(fv3)

	if _, ok := featureMap3["volume_spike_ratio"]; ok {
		t.Error("volume_spike_ratio should not be ready when Samples < 5")
	}

	// Test zero volume: should return 0
	s4 := newTestStock()
	s4.CurrentSlotVol = 0
	s4.CurrentSlot = 2
	s4.CurrentSlotSet = true
	s4.VolumeSlot = map[int]VolumeSlotBaseline{
		2: {Mean: 50000, StdDev: 5000, Samples: 10},
	}
	fv4 := r.ComputeTriggered(s4, m, nil, TriggerTick)
	featureMap4 := r.ToMap(fv4)
	r.ReleaseVector(fv4)

	if v, ok := featureMap4["volume_spike_ratio"]; !ok {
		t.Error("volume_spike_ratio should be ready even with zero volume")
	} else if v != 0 {
		t.Errorf("volume_spike_ratio = %f, want 0 for zero volume", v)
	}
}

func TestClassifiedVolume5m(t *testing.T) {
	r := NewDefaultRegistry()
	m := &MarketState{}

	// Setup: stock with BuyVol5m=3000, SellVol5m=2500
	now := time.Date(2026, 3, 24, 10, 0, 0, 0, time.Local)
	s := newTestStock()
	s.BuyVol5m.Add(now, 3000)
	s.SellVol5m.Add(now, 2500)

	fv := r.ComputeTriggered(s, m, nil, TriggerTick)
	featureMap := r.ToMap(fv)
	r.ReleaseVector(fv)

	if v, ok := featureMap["classified_volume_5m"]; !ok {
		t.Error("classified_volume_5m not in feature map")
	} else if v != 5500 {
		t.Errorf("classified_volume_5m = %f, want 5500", v)
	}

	// Test empty: both zero → 0
	s2 := newTestStock()
	fv2 := r.ComputeTriggered(s2, m, nil, TriggerTick)
	featureMap2 := r.ToMap(fv2)
	r.ReleaseVector(fv2)

	if v, ok := featureMap2["classified_volume_5m"]; !ok {
		t.Error("classified_volume_5m not in feature map")
	} else if v != 0 {
		t.Errorf("classified_volume_5m = %f, want 0", v)
	}
}
