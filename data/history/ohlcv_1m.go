package history

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/karanshergill/algotrix-go/models"
)

// Fetch1mOHLCV fetches 1-minute candles for a Fyers symbol (e.g. "NSE:SBIN-EQ").
// Returns candles mapped to the given ISIN.
// Fyers allows max 100 days per request for minute resolutions.
func Fetch1mOHLCV(authToken, fySymbol, isin string, from, to time.Time) ([]models.OHLCV, error) {
	url := fmt.Sprintf("%s?symbol=%s&resolution=1&range_from=%s&range_to=%s&date_format=1&cont_flag=1",
		historyURL, fySymbol, from.Format("2006-01-02"), to.Format("2006-01-02"))

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", authToken)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("fetch 1m history %s: %w", fySymbol, err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var result historyResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("parse 1m history %s: %w", fySymbol, err)
	}

	if result.S != "ok" {
		return nil, fmt.Errorf("1m history API error for %s: %s", fySymbol, result.Message)
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
