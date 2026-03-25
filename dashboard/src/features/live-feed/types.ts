export interface TickData {
  symbol: string
  isin: string
  ltp?: number
  volume?: number
  open?: number
  high?: number
  low?: number
  prevClose?: number
  change?: number
  changePct?: number
  ts: number
}

export interface DepthLevel {
  price: number
  qty: number
  orders: number
}

export interface DepthData {
  symbol: string
  isin: string
  bestBid?: number
  bestAsk?: number
  tbq?: number
  tsq?: number
  bids?: DepthLevel[]
  asks?: DepthLevel[]
  ts: number
}

export interface SymbolSearchResult {
  isin: string
  symbol: string
  name: string | null
  sector_macro: string | null
  sector: string | null
  industry: string | null
  industry_basic: string | null
}

export interface SubscribedSymbol {
  symbol: string // fy_symbol e.g. "NSE:RELIANCE-EQ"
  isin: string
  name: string | null
}
