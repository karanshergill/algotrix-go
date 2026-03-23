package features

import (
	"testing"
	"time"
)

func TestFeedGuard_AcceptsValidTick(t *testing.T) {
	g := NewFeedGuard(DefaultGuardConfig())
	now := time.Now()

	// First tick to establish baseline
	ok, reason := g.ValidateTick("INE001", 100.0, 1000, now)
	if !ok {
		t.Fatalf("first tick rejected: %s", reason)
	}

	// Second valid tick
	ok, reason = g.ValidateTick("INE001", 101.0, 1100, now.Add(time.Second))
	if !ok {
		t.Fatalf("valid tick rejected: %s", reason)
	}
}

func TestFeedGuard_RejectsZeroLTP(t *testing.T) {
	g := NewFeedGuard(DefaultGuardConfig())
	now := time.Now()

	ok, reason := g.ValidateTick("INE001", 0, 1000, now)
	if ok {
		t.Fatal("expected rejection for zero LTP")
	}
	if reason != "ltp <= 0" {
		t.Fatalf("unexpected reason: %s", reason)
	}
}

func TestFeedGuard_RejectsNegativeLTP(t *testing.T) {
	g := NewFeedGuard(DefaultGuardConfig())
	now := time.Now()

	ok, reason := g.ValidateTick("INE001", -5.0, 1000, now)
	if ok {
		t.Fatal("expected rejection for negative LTP")
	}
	if reason != "ltp <= 0" {
		t.Fatalf("unexpected reason: %s", reason)
	}
}

func TestFeedGuard_RejectsBackwardTimestamp(t *testing.T) {
	g := NewFeedGuard(DefaultGuardConfig())
	now := time.Now()

	g.ValidateTick("INE001", 100.0, 1000, now)

	ok, reason := g.ValidateTick("INE001", 101.0, 1100, now.Add(-time.Second))
	if ok {
		t.Fatal("expected rejection for backward timestamp")
	}
	if reason != "timestamp went backward" {
		t.Fatalf("unexpected reason: %s", reason)
	}
}

func TestFeedGuard_RejectsPriceJump(t *testing.T) {
	g := NewFeedGuard(DefaultGuardConfig())
	now := time.Now()

	g.ValidateTick("INE001", 100.0, 1000, now)

	// 25% jump exceeds default 20% threshold
	ok, _ := g.ValidateTick("INE001", 125.0, 1100, now.Add(time.Second))
	if ok {
		t.Fatal("expected rejection for price jump > 20%")
	}
}

func TestFeedGuard_AcceptsFirstTick(t *testing.T) {
	g := NewFeedGuard(DefaultGuardConfig())
	now := time.Now()

	// First tick with any reasonable LTP should always pass — no prior reference
	ok, reason := g.ValidateTick("INE001", 5000.0, 500000, now)
	if !ok {
		t.Fatalf("first tick should always pass, got rejection: %s", reason)
	}
}

func TestFeedGuard_HandlesVolumeReset(t *testing.T) {
	g := NewFeedGuard(DefaultGuardConfig())
	now := time.Now()

	g.ValidateTick("INE001", 100.0, 5000, now)

	// Volume goes backward (reconnect scenario) — should still accept
	ok, reason := g.ValidateTick("INE001", 101.0, 100, now.Add(time.Second))
	if !ok {
		t.Fatalf("volume reset should be accepted with AllowVolumeReset=true, got: %s", reason)
	}
}
