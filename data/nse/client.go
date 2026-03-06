package nse

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"sync"
	"time"
)

const (
	baseURL   = "https://www.nseindia.com"
	userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

	// Rate limiting: minimum gap between requests.
	minRequestGap = 500 * time.Millisecond

	// Cookies expire; refresh after this duration.
	cookieMaxAge = 5 * time.Minute
)

// Client handles authenticated HTTP requests to NSE India.
type Client struct {
	http       *http.Client
	mu         sync.Mutex
	lastReq    time.Time
	cookieTime time.Time
}

// NewClient creates a new NSE client with cookie jar.
func NewClient() (*Client, error) {
	jar, err := cookiejar.New(nil)
	if err != nil {
		return nil, fmt.Errorf("creating cookie jar: %w", err)
	}

	return &Client{
		http: &http.Client{
			Jar:     jar,
			Timeout: 15 * time.Second,
		},
	}, nil
}

// refreshCookies hits the NSE homepage to obtain fresh session cookies.
func (c *Client) refreshCookies() error {
	req, err := http.NewRequest("GET", baseURL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept", "text/html")

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("fetching NSE cookies: %w", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)

	c.cookieTime = time.Now()
	return nil
}

// get performs a rate-limited, cookie-authenticated GET request.
// Returns the raw response body.
func (c *Client) get(endpoint string) ([]byte, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	// Rate limiting.
	since := time.Since(c.lastReq)
	if since < minRequestGap {
		time.Sleep(minRequestGap - since)
	}

	// Refresh cookies if stale or missing.
	if time.Since(c.cookieTime) > cookieMaxAge {
		if err := c.refreshCookies(); err != nil {
			return nil, err
		}
	}

	url := baseURL + endpoint
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Referer", baseURL)

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("GET %s: %w", endpoint, err)
	}
	defer resp.Body.Close()
	c.lastReq = time.Now()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GET %s: status %d", endpoint, resp.StatusCode)
	}

	return io.ReadAll(resp.Body)
}

// getJSON performs a GET and unmarshals the JSON response into dst.
func (c *Client) getJSON(endpoint string, dst any) error {
	body, err := c.get(endpoint)
	if err != nil {
		return err
	}
	return json.Unmarshal(body, dst)
}
