package operations

import (
	"context"
	"fmt"

	qdb "github.com/questdb/go-questdb-client/v4"
	"github.com/karanshergill/algotrix-go/models"
)

const ohlcvDailyTable = "nse_cm_ohlcv_1d"

const flushChunkSize = 10000

// WriteOHLCV writes a batch of candles to QuestDB via ILP, flushing every 10k rows.
func WriteOHLCV(ctx context.Context, sender qdb.LineSender, candles []models.OHLCV) error {
	for i, c := range candles {
		err := sender.
			Table(ohlcvDailyTable).
			Symbol("isin", c.ISIN).
			Float64Column("open", c.Open).
			Float64Column("high", c.High).
			Float64Column("low", c.Low).
			Float64Column("close", c.Close).
			Int64Column("volume", c.Volume).
			At(ctx, c.Timestamp)
		if err != nil {
			return fmt.Errorf("write candle %s %s: %w", c.ISIN, c.Timestamp.Format("2006-01-02"), err)
		}

		if (i+1)%flushChunkSize == 0 {
			if err := sender.Flush(ctx); err != nil {
				return fmt.Errorf("flush OHLCV chunk: %w", err)
			}
		}
	}

	if err := sender.Flush(ctx); err != nil {
		return fmt.Errorf("flush OHLCV: %w", err)
	}

	return nil
}
