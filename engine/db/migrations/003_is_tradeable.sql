-- Add is_tradeable column to symbols table.
-- Pre-computed daily by cron; used by go-feed to select qualified stocks.
ALTER TABLE symbols ADD COLUMN IF NOT EXISTS is_tradeable BOOLEAN NOT NULL DEFAULT false;

-- Initial population: stocks with sufficient price, volume, turnover, and trading days.
UPDATE symbols s SET is_tradeable = true
FROM (
  SELECT DISTINCT isin
  FROM nse_cm_bhavcopy
  WHERE date >= CURRENT_DATE - INTERVAL '20 days'
  GROUP BY isin
  HAVING
    MAX(close) >= 100
    AND AVG(volume) >= 100000
    AND AVG(traded_value) >= 50000000
    AND COUNT(DISTINCT date) >= (
      SELECT COUNT(DISTINCT date) FROM nse_cm_bhavcopy
      WHERE date >= CURRENT_DATE - INTERVAL '20 days'
    )
) q
WHERE s.isin = q.isin AND s.status = 'active';
