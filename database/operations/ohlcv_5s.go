package operations

import (
	"context"
	"fmt"

	qdb "github.com/questdb/go-questdb-client/v4"
	"github.com/karanshergill/algotrix-go/models"
)

const ohlcv5sTable = "nse_cm_ohlcv_5s"

const flush5sChunkSize = 10000

// Write5sOHLCV writes a batch of 5-second candles to QuestDB via ILP, flushing every 10k rows.
func Write5sOHLCV(ctx context.Context, sender qdb.LineSender, candles []models.OHLCV) error {
	for i, c := range candles {
		err := sender.
			Table(ohlcv5sTable).
			Symbol("isin", c.ISIN).
			Float64Column("open", c.Open).
			Float64Column("high", c.High).
			Float64Column("low", c.Low).
			Float64Column("close", c.Close).
			Int64Column("volume", c.Volume).
			At(ctx, c.Timestamp)
		if err != nil {
			return fmt.Errorf("write 5s candle %s %s: %w", c.ISIN, c.Timestamp.Format("2006-01-02 15:04:05"), err)
		}

		if (i+1)%flush5sChunkSize == 0 {
			if err := sender.Flush(ctx); err != nil {
				return fmt.Errorf("flush 5s OHLCV chunk: %w", err)
			}
		}
	}

	if err := sender.Flush(ctx); err != nil {
		return fmt.Errorf("flush 5s OHLCV: %w", err)
	}

	return nil
}
