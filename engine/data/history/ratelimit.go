package history

import (
	"sync"
	"time"
)

// rateLimiter enforces a maximum request rate across all goroutines.
// Uses a token bucket approach with a refill ticker.
type rateLimiter struct {
	mu       sync.Mutex
	tokens   int
	maxBurst int
	ticker   *time.Ticker
	done     chan struct{}
}

// newRateLimiter creates a rate limiter that allows `perSecond` requests/sec
// with a burst capacity of `burst`.
func newRateLimiter(perSecond int, burst int) *rateLimiter {
	rl := &rateLimiter{
		tokens:   burst,
		maxBurst: burst,
		done:     make(chan struct{}),
	}

	interval := time.Second / time.Duration(perSecond)
	rl.ticker = time.NewTicker(interval)

	go func() {
		for {
			select {
			case <-rl.ticker.C:
				rl.mu.Lock()
				if rl.tokens < rl.maxBurst {
					rl.tokens++
				}
				rl.mu.Unlock()
			case <-rl.done:
				return
			}
		}
	}()

	return rl
}

// wait blocks until a token is available, then consumes it.
func (rl *rateLimiter) wait() {
	for {
		rl.mu.Lock()
		if rl.tokens > 0 {
			rl.tokens--
			rl.mu.Unlock()
			return
		}
		rl.mu.Unlock()
		time.Sleep(50 * time.Millisecond)
	}
}

// stop shuts down the refill goroutine.
func (rl *rateLimiter) stop() {
	rl.ticker.Stop()
	close(rl.done)
}

// globalRL is the shared rate limiter for all Fyers API calls.
// Fyers limit: 10/sec, 200/min. We use 8/sec with burst of 5 for safety margin.
var globalRL = newRateLimiter(8, 5)
