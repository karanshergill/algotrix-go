package symbols

import (
    "context"
    "encoding/csv"
    "fmt"
    "io"
    "log"
	"net/http"
    "strconv"
    "time"

    "github.com/jackc/pgx/v5"
    "github.com/jackc/pgx/v5/pgxpool"
)

// Download fetches the CSV and parses it into Symbol structs.
func Download() ([]Symbol, error) {
    client := &http.Client{Timeout: 30 * time.Second}

    resp, err := client.Get(CSVURL)
    if err != nil {
        return nil, fmt.Errorf("failed to download CSV: %w", err)
	}
    defer resp.Body.Close()

    if resp.StatusCode != http.StatusOK {
        return nil, fmt.Errorf("unexpected status code: %d", resp.StatusCode)
    }

    reader := csv.NewReader(resp.Body)
    reader.FieldsPerRecord = -1 // variable fields

    var symbols []Symbol

    for {
        record, err := reader.Read()
        if err == io.EOF {
			break
        }
        if err != nil {
            log.Printf("skipping malformed row: %v", err)
            continue
        }

        if len(record) <= idxSymbol {
            continue
        }

        fyToken, err := strconv.ParseInt(record[idxFyToken], 10, 64)
        if err != nil {
            log.Printf("skipping row, invalid fy_token %q: %v", record[idxFyToken], err)
            continue
		}

        symbols = append(symbols, Symbol{
            FyToken: fyToken,
            Symbol:  record[idxSymbol],
            Name:    record[idxName],
            ISIN:    record[idxISIN],
        })
    }

    log.Printf("parsed %d symbols from CSV", len(symbols))
    return symbols, nil
}

// Upsert loads symbols into the database using batch upsert.
func Upsert(ctx context.Context, pool *pgxpool.Pool, syms []Symbol) (int64, error) {
    query := fmt.Sprintf(`
        INSERT INTO %s (%s, %s, %s, %s)
        VALUES (@%s, @%s, @%s, @%s)
        ON CONFLICT (%s) DO UPDATE SET
            %s = EXCLUDED.%s,
            %s = EXCLUDED.%s,
            %s = EXCLUDED.%s`,
        TableName,
        ColFyToken, ColSymbol, ColName, ColISIN,
        ColFyToken, ColSymbol, ColName, ColISIN,
        ColFyToken,
        ColSymbol, ColSymbol,
        ColName, ColName,
        ColISIN, ColISIN,
	)

    batch := &pgx.Batch{}
    for _, s := range syms {
        args := pgx.NamedArgs{
            ColFyToken: s.FyToken,
            ColSymbol:  s.Symbol,
            ColName:    s.Name,
            ColISIN:    s.ISIN,
        }
        batch.Queue(query, args)
    }

    br := pool.SendBatch(ctx, batch)
    defer br.Close()
	var count int64
    for range syms {
        ct, err := br.Exec()
        if err != nil {
            return count, fmt.Errorf("batch exec error at row %d: %w", count, err)
        }
        count += ct.RowsAffected()
    }

    log.Printf("upserted %d rows into %s", count, TableName)
    return count, nil
}

// Load downloads the CSV and upserts all symbols. One call does everything.
func Load(ctx context.Context, pool *pgxpool.Pool) error {
    syms, err := Download()
    if err != nil {
        return err
    }

    _, err = Upsert(ctx, pool, syms)
    return err
}