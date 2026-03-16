export type StockScore = {
  ISIN: string
  MADTV: number
  Amihud: number
  ATRPct: number
  Parkinson: number
  TradeSize: number
  TradingDays: number
  PctMADTV: number
  PctAmihud: number
  PctATRPct: number
  PctParkinson: number
  PctTradeSize: number
  Composite: number
}

export type BuildResult = {
  Qualified: StockScore[]
  Rejected: number
  Total: number
  Symbols: Record<string, string>
}

export type BreakdownItem = {
  metric: string
  percentile: number
  weight: number
  points: number
}

export type ExplainResult = {
  symbol: string
  isin: string
  lookback: number
  coverage: number
  status: 'qualified' | 'rejected'
  rank: number | null
  totalQualified: number
  raw?: {
    madtv: number
    amihud: number
    atrPct: number
    parkinson: number
    tradeSize: number
    tradingDays: number
  }
  percentiles?: {
    pctMADTV: number
    pctAmihud: number
    pctATRPct: number
    pctParkinson: number
    pctTradeSize: number
  }
  composite?: number
  breakdown?: BreakdownItem[]
  strengths?: string[]
  weaknesses?: string[]
}

export type BuildParams = {
  lookback: number
  fnoOnly: boolean
  madtvFloor: number // in rupees (e.g. 1e9 = ₹100Cr)
}
