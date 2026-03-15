package nse

import (
	"archive/zip"
	"bytes"
	"database/sql"
	"encoding/csv"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// BhavcopyRow represents a single equity row from the NSE CM bhavcopy.
type BhavcopyRow struct {
	ISIN        string
	Date        time.Time
	Open        float64
	High        float64
	Low         float64
	Close       float64
	LastPrice   float64
	PrevClose   float64
	Volume      int64
	TradedValue float64
	NumTrades   int64
}

// FetchBhavcopy downloads and parses the NSE CM bhavcopy ZIP for the given date.
// Only equity (SctySrs == "EQ") rows are returned.
func FetchBhavcopy(date time.Time) ([]BhavcopyRow, error) {
	url := fmt.Sprintf(
		"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_%s_F_0000.csv.zip",
		date.Format("20060102"),
	)

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, fmt.Errorf("creating request: %w", err)
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("downloading bhavcopy: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("downloading bhavcopy: status %d", resp.StatusCode)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading bhavcopy response: %w", err)
	}

	// Open ZIP from memory.
	zr, err := zip.NewReader(bytes.NewReader(body), int64(len(body)))
	if err != nil {
		return nil, fmt.Errorf("opening zip: %w", err)
	}

	if len(zr.File) == 0 {
		return nil, fmt.Errorf("zip archive is empty")
	}

	f, err := zr.File[0].Open()
	if err != nil {
		return nil, fmt.Errorf("opening csv in zip: %w", err)
	}
	defer f.Close()

	reader := csv.NewReader(f)
	// Read and discard header.
	if _, err := reader.Read(); err != nil {
		return nil, fmt.Errorf("reading csv header: %w", err)
	}

	var rows []BhavcopyRow
	for {
		record, err := reader.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("reading csv row: %w", err)
		}

		// Filter: only EQ series.
		if len(record) <= 26 {
			continue
		}
		if strings.TrimSpace(record[8]) != "EQ" {
			continue
		}

		row := BhavcopyRow{
			ISIN: strings.TrimSpace(record[6]),
		}

		row.Date, _ = time.Parse("2006-01-02", strings.TrimSpace(record[0]))
		row.Open = parseFloatSafe(record[14])
		row.High = parseFloatSafe(record[15])
		row.Low = parseFloatSafe(record[16])
		row.Close = parseFloatSafe(record[17])
		row.LastPrice = parseFloatSafe(record[18])
		row.PrevClose = parseFloatSafe(record[19])
		row.Volume = parseIntSafe(record[24])
		row.TradedValue = parseFloatSafe(record[25])
		row.NumTrades = parseIntSafe(record[26])

		rows = append(rows, row)
	}

	log.Printf("Parsed %d EQ rows from bhavcopy for %s", len(rows), date.Format("2006-01-02"))
	return rows, nil
}

func parseFloatSafe(s string) float64 {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0
	}
	v, _ := strconv.ParseFloat(s, 64)
	return v
}

func parseIntSafe(s string) int64 {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0
	}
	v, _ := strconv.ParseInt(s, 10, 64)
	return v
}

// StoreBhavcopy inserts bhavcopy rows into the nse_cm_bhavcopy table.
// Uses batch INSERTs of 500 rows with ON CONFLICT DO NOTHING.
// Returns the total number of rows inserted.
func StoreBhavcopy(db *sql.DB, rows []BhavcopyRow) (int64, error) {
	const batchSize = 500
	var totalInserted int64

	for i := 0; i < len(rows); i += batchSize {
		end := i + batchSize
		if end > len(rows) {
			end = len(rows)
		}
		batch := rows[i:end]

		var sb strings.Builder
		sb.WriteString("INSERT INTO nse_cm_bhavcopy (isin, date, open, high, low, close, last_price, prev_close, volume, traded_value, num_trades) VALUES ")

		args := make([]any, 0, len(batch)*11)
		for j, row := range batch {
			if j > 0 {
				sb.WriteString(", ")
			}
			offset := j * 11
			fmt.Fprintf(&sb, "($%d, $%d, $%d, $%d, $%d, $%d, $%d, $%d, $%d, $%d, $%d)",
				offset+1, offset+2, offset+3, offset+4, offset+5, offset+6,
				offset+7, offset+8, offset+9, offset+10, offset+11,
			)
			args = append(args, row.ISIN, row.Date, row.Open, row.High, row.Low, row.Close,
				row.LastPrice, row.PrevClose, row.Volume, row.TradedValue, row.NumTrades)
		}

		sb.WriteString(" ON CONFLICT (isin, date) DO NOTHING")

		result, err := db.Exec(sb.String(), args...)
		if err != nil {
			return totalInserted, fmt.Errorf("inserting batch at offset %d: %w", i, err)
		}

		n, _ := result.RowsAffected()
		totalInserted += n
	}

	return totalInserted, nil
}
