export type StockScore = {
  ISIN: string
  MADTV: number
  Amihud: number
  ATRPct: number
  Parkinson: number
  TradeSize: number
  ADRPct: number
  RangeEff: number
  Momentum5D: number
  TradingDays: number
  PctMADTV: number
  PctAmihud: number
  PctATRPct: number
  PctParkinson: number
  PctTradeSize: number
  PctADRPct: number
  PctRangeEff: number
  PctMomentum: number
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
    adrPct: number
    rangeEff: number
    momentum5d: number
    tradingDays: number
  }
  percentiles?: {
    pctMADTV: number
    pctAmihud: number
    pctATRPct: number
    pctParkinson: number
    pctTradeSize: number
    pctADRPct: number
    pctRangeEff: number
    pctMomentum: number
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
