-- Migration 003: indices table
-- Stores NSE indices for feed subscription and calculations

CREATE TABLE IF NOT EXISTS indices (
  id         SERIAL PRIMARY KEY,
  symbol     TEXT NOT NULL UNIQUE,    -- e.g. NSE:NIFTY50-INDEX
  name       TEXT NOT NULL,           -- e.g. Nifty 50
  fy_symbol  TEXT NOT NULL UNIQUE,    -- Fyers symbol (same as symbol for indices)
  category   TEXT NOT NULL,           -- broad | sectoral | thematic
  is_active  BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Seed data
INSERT INTO indices (symbol, name, fy_symbol, category) VALUES
  ('NSE:NIFTY50-INDEX',          'Nifty 50',            'NSE:NIFTY50-INDEX',          'broad'),
  ('NSE:NIFTYNXT50-INDEX',       'Nifty Next 50',       'NSE:NIFTYNXT50-INDEX',       'broad'),
  ('NSE:NIFTY500-INDEX',         'Nifty 500',           'NSE:NIFTY500-INDEX',         'broad'),
  ('NSE:NIFTYMIDCAP150-INDEX',   'Nifty Midcap 150',    'NSE:NIFTYMIDCAP150-INDEX',   'broad'),
  ('NSE:NIFTYSMALLCAP250-INDEX', 'Nifty Smallcap 250',  'NSE:NIFTYSMALLCAP250-INDEX', 'broad'),
  ('NSE:NIFTYBANK-INDEX',        'Nifty Bank',          'NSE:NIFTYBANK-INDEX',        'sectoral'),
  ('NSE:FINNIFTY-INDEX',         'Nifty Fin Service',   'NSE:FINNIFTY-INDEX',         'sectoral'),
  ('NSE:MIDCPNIFTY-INDEX',       'Nifty Midcap Select', 'NSE:MIDCPNIFTY-INDEX',       'sectoral'),
  ('NSE:NIFTYIT-INDEX',          'Nifty IT',            'NSE:NIFTYIT-INDEX',          'sectoral'),
  ('NSE:NIFTYPHARMA-INDEX',      'Nifty Pharma',        'NSE:NIFTYPHARMA-INDEX',      'sectoral'),
  ('NSE:NIFTYAUTO-INDEX',        'Nifty Auto',          'NSE:NIFTYAUTO-INDEX',        'sectoral'),
  ('NSE:NIFTYMETAL-INDEX',       'Nifty Metal',         'NSE:NIFTYMETAL-INDEX',       'sectoral'),
  ('NSE:NIFTYREALTY-INDEX',      'Nifty Realty',        'NSE:NIFTYREALTY-INDEX',      'sectoral')
ON CONFLICT (symbol) DO NOTHING;
