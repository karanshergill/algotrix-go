package history

import (
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"
)

const maxRetries = 3

// doFyersRequest performs an HTTP GET to the Fyers API with retry logic.
// Retries on HTTP 429 (rate limit), 5xx (server error), and network errors.
// Does NOT retry on 4xx (except 429) as those indicate client errors.
func doFyersRequest(authToken, url string) ([]byte, error) {
	delay := 1 * time.Second

	var lastErr error
	for attempt := 0; attempt <= maxRetries; attempt++ {
		if attempt > 0 {
			fmt.Printf("  RETRY [%d/%d]: %v, waiting %v\n", attempt, maxRetries, lastErr, delay)
			time.Sleep(delay)
			delay *= 2
		}

		// Wait for rate limiter token before making request
		globalRL.wait()

		req, err := http.NewRequest("GET", url, nil)
		if err != nil {
			return nil, err
		}
		req.Header.Set("Authorization", authToken)

		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			// Network error — retry.
			lastErr = fmt.Errorf("network error: %w", err)
			continue
		}

		body, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			lastErr = fmt.Errorf("read body: %w", err)
			continue
		}

		switch {
		case resp.StatusCode == http.StatusTooManyRequests:
			// Rate limited. Use Retry-After header if present.
			if ra := resp.Header.Get("Retry-After"); ra != "" {
				if secs, err := strconv.Atoi(ra); err == nil {
					delay = time.Duration(secs) * time.Second
				}
			}
			lastErr = fmt.Errorf("HTTP 429 rate limited")
			continue
		case resp.StatusCode >= 500:
			lastErr = fmt.Errorf("HTTP %d server error", resp.StatusCode)
			continue
		case resp.StatusCode >= 400:
			// 4xx (non-429) client error — do not retry.
			return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
		}

		return body, nil
	}

	return nil, fmt.Errorf("failed after %d retries: %w", maxRetries, lastErr)
}
