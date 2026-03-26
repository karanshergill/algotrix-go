package history

import (
	"encoding/json"
	"fmt"
	"net/url"
	"time"

	"github.com/karanshergill/algotrix-go/models"
)

// Fetch5mOHLCV fetches 5-minute candles for a Fyers symbol (e.g. "NSE:SBIN-EQ").
// Returns candles mapped to the given ISIN.
// Fyers allows max 100 days per request for minute resolutions.
func Fetch5mOHLCV(authToken, fySymbol, isin string, from, to time.Time) ([]models.OHLCV, error) {
	reqURL := fmt.Sprintf("%s?symbol=%s&resolution=5&range_from=%s&range_to=%s&date_format=1&cont_flag=1",
		historyURL, url.QueryEscape(fySymbol), from.Format("2006-01-02"), to.Format("2006-01-02"))

	body, err := doFyersRequest(authToken, reqURL)
	if err != nil {
		return nil, fmt.Errorf("fetch 5m history %s: %w", fySymbol, err)
	}

	var result historyResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("parse 5m history %s: %w", fySymbol, err)
	}

	if result.S != "ok" {
		return nil, fmt.Errorf("5m history API error for %s: %s", fySymbol, result.Message)
	}

	candles := make([]models.OHLCV, 0, len(result.Candles))
	for _, c := range result.Candles {
		if len(c) < 6 {
			continue
		}
		candles = append(candles, models.OHLCV{
			ISIN:      isin,
			Timestamp: time.Unix(int64(c[0]), 0),
			Open:      c[1],
			High:      c[2],
			Low:       c[3],
			Close:     c[4],
			Volume:    int64(c[5]),
		})
	}

	return candles, nil
}
