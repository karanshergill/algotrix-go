package features

import (
	"sync"
	"time"
)

// ---------------------------------------------------------------------------
// TriggerType — bitflags indicating when a feature should be computed.
// ---------------------------------------------------------------------------

// TriggerType is a bitmask for feature trigger policies.
type TriggerType int

const (
	TriggerTick  TriggerType = 1 << iota // compute on tick events
	TriggerDepth                         // compute on depth events
	TriggerTimer                         // compute on 1s timer
)

// ---------------------------------------------------------------------------
// FeatureDef — a single feature definition with trigger, readiness, compute.
// ---------------------------------------------------------------------------

// FeatureDef describes a feature: its trigger policy, readiness check, and compute function.
type FeatureDef struct {
	Name     string
	Version  int
	Category string // "price", "volume", "book", "breadth", "sector"
	Trigger  TriggerType
	Ready    func(s *StockState, m *MarketState) bool
	Compute  func(s *StockState, m *MarketState, sec *SectorState) float64
}

// ---------------------------------------------------------------------------
// FeatureVector — pooled slice of computed feature values.
// ---------------------------------------------------------------------------

// FeatureVector holds computed values for all registered features.
type FeatureVector struct {
	Values  []float64
	Ready   []bool
	Version int
}

// ---------------------------------------------------------------------------
// Registry — ordered collection of feature definitions with trigger indices.
// ---------------------------------------------------------------------------

// Registry holds all registered features and dispatches computation by trigger.
type Registry struct {
	features      []FeatureDef
	nameIdx       map[string]int
	version       int
	tickFeatures  []int // indices into features
	depthFeatures []int
	timerFeatures []int
	pool          sync.Pool
}

// NewRegistry creates an empty Registry.
func NewRegistry() *Registry {
	return &Registry{
		nameIdx: make(map[string]int),
		version: 1,
	}
}

// Register adds a feature definition to the registry.
// Must be called before buildTriggerIndex.
func (r *Registry) Register(def FeatureDef) {
	idx := len(r.features)
	r.features = append(r.features, def)
	r.nameIdx[def.Name] = idx
}

// buildTriggerIndex groups features by trigger type and initializes the pool.
func (r *Registry) buildTriggerIndex() {
	r.tickFeatures = nil
	r.depthFeatures = nil
	r.timerFeatures = nil

	for i, f := range r.features {
		if f.Trigger&TriggerTick != 0 {
			r.tickFeatures = append(r.tickFeatures, i)
		}
		if f.Trigger&TriggerDepth != 0 {
			r.depthFeatures = append(r.depthFeatures, i)
		}
		if f.Trigger&TriggerTimer != 0 {
			r.timerFeatures = append(r.timerFeatures, i)
		}
	}

	n := len(r.features)
	r.pool = sync.Pool{
		New: func() any {
			return &FeatureVector{
				Values: make([]float64, n),
				Ready:  make([]bool, n),
			}
		},
	}
}

// ComputeTriggered runs only features matching the trigger type.
// Returns a pooled FeatureVector — caller must call ReleaseVector when done.
func (r *Registry) ComputeTriggered(s *StockState, m *MarketState, sec *SectorState, trigger TriggerType) *FeatureVector {
	fv := r.pool.Get().(*FeatureVector)
	fv.Version = r.version

	// Reset all values
	for i := range fv.Values {
		fv.Values[i] = 0
		fv.Ready[i] = false
	}

	var indices []int
	switch trigger {
	case TriggerTick:
		indices = r.tickFeatures
	case TriggerDepth:
		indices = r.depthFeatures
	case TriggerTimer:
		indices = r.timerFeatures
	}

	for _, i := range indices {
		f := r.features[i]
		if f.Ready != nil && !f.Ready(s, m) {
			fv.Values[i] = 0
			fv.Ready[i] = false
		} else {
			fv.Values[i] = f.Compute(s, m, sec)
			fv.Ready[i] = true
		}
	}
	return fv
}

// ToMap converts a FeatureVector to a name→value map (only ready features).
func (r *Registry) ToMap(fv *FeatureVector) map[string]float64 {
	out := make(map[string]float64, len(r.features))
	for i, f := range r.features {
		if fv.Ready[i] {
			out[f.Name] = fv.Values[i]
		}
	}
	return out
}

// ReleaseVector returns a FeatureVector to the pool.
func (r *Registry) ReleaseVector(fv *FeatureVector) {
	r.pool.Put(fv)
}

// FeatureNames returns an ordered list of all registered feature names.
func (r *Registry) FeatureNames() []string {
	names := make([]string, len(r.features))
	for i, f := range r.features {
		names[i] = f.Name
	}
	return names
}

// ---------------------------------------------------------------------------
// timeToSlot — converts time to 5-min slot index (9:15=0, 9:20=1, etc.)
// ---------------------------------------------------------------------------

func timeToSlot(t time.Time) int {
	h, m, _ := t.Clock()
	mins := h*60 + m - 9*60 - 15 // minutes since 9:15
	if mins < 0 {
		return 0
	}
	return mins / 5
}

// ---------------------------------------------------------------------------
// NewDefaultRegistry — creates a registry with all 19 features registered.
// ---------------------------------------------------------------------------

// NewDefaultRegistry creates a Registry with all standard features.
func NewDefaultRegistry() *Registry {
	r := NewRegistry()
	RegisterPriceFeatures(r)
	RegisterVolumeFeatures(r)
	RegisterBookFeatures(r)
	RegisterBreadthFeatures(r)
	RegisterSectorFeatures(r)
	r.buildTriggerIndex()
	return r
}
