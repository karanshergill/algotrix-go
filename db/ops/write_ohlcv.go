package ops

import (
	"context"
	"fmt"

	qdb "github.com/questdb/go-questdb-client/v4"
	"github.com/karanshergill/algotrix-go/models"
)

const flushChunkSize = 10000

// WriteOHLCV writes a batch of candles to a specified QuestDB table via ILP, flushing every 10k rows.
func WriteOHLCV(ctx context.Context, sender qdb.LineSender, table string, candles []models.OHLCV) error {
	for i, c := range candles {
		err := sender.
			Table(table).
			Symbol("isin", c.ISIN).
			Float64Column("open", c.Open).
			Float64Column("high", c.High).
			Float64Column("low", c.Low).
			Float64Column("close", c.Close).
			Int64Column("volume", c.Volume).
			At(ctx, c.Timestamp)
		if err != nil {
			return fmt.Errorf("write candle to %s %s %s: %w", table, c.ISIN, c.Timestamp.Format("2006-01-02 15:04:05"), err)
		}

		if (i+1)%flushChunkSize == 0 {
			if err := sender.Flush(ctx); err != nil {
				return fmt.Errorf("flush %s chunk: %w", table, err)
			}
		}
	}

	if err := sender.Flush(ctx); err != nil {
		return fmt.Errorf("flush %s: %w", table, err)
	}

	return nil
}
