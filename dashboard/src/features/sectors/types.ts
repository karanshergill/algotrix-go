export type SectorLevel = 'macro' | 'sector' | 'industry' | 'sub_industry'

export type SectorGroup = {
  group_name: string
  stock_count: number
  score: number | null

  ret_1d: number | null
  ret_1w: number | null
  ret_1m: number | null
  ret_3m: number | null
  ret_6m: number | null
  ret_1y: number | null
  adv_count: number
  dec_count: number
  unch_count: number
  vol_total_1d: number | null
  vol_avg_20d: number | null
  vol_ratio: number | null
}

export type SectorStrengthResponse = {
  date: string | null
  level: SectorLevel
  groups: SectorGroup[]
}

export type StockMatch = {
  isin: string
  symbol: string
  name: string
  sector_macro: string | null
  sector: string | null
  industry: string | null
  industry_basic: string | null
}

export type StockSearchResponse = {
  query: string
  limit: number
  matches: StockMatch[]
}

export type GroupChainNode = {
  level: SectorLevel
  group_name: string | null
  stock_count: number
  score: number | null
  ret_1d: number | null
  adv_count: number
  dec_count: number
  vol_ratio: number | null
}

export type GroupChainPeer = {
  isin: string
  symbol: string
  name: string
}

export type GroupChainResponse = {
  stock: StockMatch
  chain: GroupChainNode[]
  peers: GroupChainPeer[]
}
