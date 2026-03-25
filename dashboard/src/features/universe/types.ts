export interface Stock {
  isin: string
  symbol: string
  name: string | null
  series: string
  sector: string | null
  marketCap: number | null
  isFnO: boolean
  indexMemberships: string[]
  lastPrice: number
  avgVolume20d: number
  avgTurnover20d: number
  tradedDays: number
}

export interface UniverseResponse {
  asOf: string | null
  tradingDays: number
  stocks: Stock[]
}

export interface FilterState {
  priceMin: number
  priceMax: number
  volumeMin: number
  turnoverMin: number
  minTradedDays: number
  series: string[]
}

export type DepthTier = 'D50' | 'D30' | 'D5' | 'Eligible' | 'none'

export interface FilteredStock extends Stock {
  tier: DepthTier
  pass: boolean
}

export interface Preset {
  name: string
  filters: FilterState
}

export const PRESETS: Preset[] = [
  {
    name: 'Conservative',
    filters: {
      priceMin: 100,
      priceMax: 3000,
      volumeMin: 500_000,
      turnoverMin: 100_000_000,
      minTradedDays: 19,
      series: ['EQ'],
    },
  },
  {
    name: 'Balanced',
    filters: {
      priceMin: 50,
      priceMax: 5000,
      volumeMin: 100_000,
      turnoverMin: 50_000_000,
      minTradedDays: 18,
      series: ['EQ'],
    },
  },
  {
    name: 'Broad',
    filters: {
      priceMin: 20,
      priceMax: 10000,
      volumeMin: 25_000,
      turnoverMin: 10_000_000,
      minTradedDays: 15,
      series: ['EQ'],
    },
  },
  {
    name: 'D30 Target',
    filters: {
      priceMin: 50,
      priceMax: 5000,
      volumeMin: 200_000,
      turnoverMin: 80_000_000,
      minTradedDays: 18,
      series: ['EQ'],
    },
  },
]

export const DEFAULT_FILTERS: FilterState = PRESETS[1].filters
