-- 004_news_indexes.sql
-- Indexes for the News & Corporate Events page queries

-- Announcements: feed query by date (range query), market-moving filter
CREATE INDEX IF NOT EXISTS idx_ann_dt ON nse_announcements(announcement_dt DESC);
CREATE INDEX IF NOT EXISTS idx_ann_symbol ON nse_announcements(symbol);
CREATE INDEX IF NOT EXISTS idx_ann_market_moving ON nse_announcements(is_market_moving)
  WHERE is_market_moving = true;

-- Block deals: feed query by date
CREATE INDEX IF NOT EXISTS idx_block_deals_date ON nse_block_deals(deal_date DESC);

-- Board meetings: upcoming query
CREATE INDEX IF NOT EXISTS idx_board_meetings_date ON nse_board_meetings(meeting_date);

-- Corporate actions: upcoming query
CREATE INDEX IF NOT EXISTS idx_corp_actions_exdate ON nse_corporate_actions(ex_date);

-- Insider trading: THE CRITICAL INDEXES for 2.99M rows
-- Aggregation query: needs transaction_date for range scan + symbol for GROUP BY
CREATE INDEX IF NOT EXISTS idx_insider_txn_date ON nse_insider_trading(transaction_date DESC);
-- Drill-down query: symbol + date range
CREATE INDEX IF NOT EXISTS idx_insider_symbol_date ON nse_insider_trading(symbol, transaction_date DESC);
-- Covering index for aggregation: avoid heap fetches on the hot path
CREATE INDEX IF NOT EXISTS idx_insider_agg ON nse_insider_trading(transaction_date, symbol, acquisition_mode, value)
  WHERE value IS NOT NULL AND value > 0;

-- Update planner statistics
ANALYZE nse_insider_trading;
