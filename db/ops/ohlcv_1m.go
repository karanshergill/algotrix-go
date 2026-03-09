package ops

import (
	"context"
	"fmt"

	qdb "github.com/questdb/go-questdb-client/v4"
	"github.com/karanshergill/algotrix-go/models"
)

const ohlcv1mTable = "nse_cm_ohlcv_1m"
const flush1mChunkSize = 10000

// Write1mOHLCV writes a batch of 1-minute candles to QuestDB via ILP, flushing every 10k rows.
func Write1mOHLCV(ctx context.Context, sender qdb.LineSender, candles []models.OHLCV) error {
	for i, c := range candles {
		err := sender.
			Table(ohlcv1mTable).
			Symbol("isin", c.ISIN).
			Float64Column("open", c.Open).
			Float64Column("high", c.High).
			Float64Column("low", c.Low).
			Float64Column("close", c.Close).
			Int64Column("volume", c.Volume).
			At(ctx, c.Timestamp)
		if err != nil {
			return fmt.Errorf("write 1m candle %s %s: %w", c.ISIN, c.Timestamp.Format("2006-01-02 15:04:05"), err)
		}

		if (i+1)%flush1mChunkSize == 0 {
			if err := sender.Flush(ctx); err != nil {
				return fmt.Errorf("flush 1m OHLCV chunk: %w", err)
			}
		}
	}

	if err := sender.Flush(ctx); err != nil {
		return fmt.Errorf("flush 1m OHLCV: %w", err)
	}

	return nil
}
