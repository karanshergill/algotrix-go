import type { Stock, FilterState, FilteredStock, DepthTier } from './types'

export interface FilterStageResult {
  stageName: string
  inputCount: number
  passCount: number
  failCount: number
}

function passesFilter(stock: Stock, filters: FilterState): boolean {
  if (stock.lastPrice < filters.priceMin || stock.lastPrice > filters.priceMax)
    return false
  if (stock.avgVolume20d < filters.volumeMin) return false
  if (stock.avgTurnover20d < filters.turnoverMin) return false
  if (stock.tradedDays < filters.minTradedDays) return false
  if (filters.series.length > 0 && !filters.series.includes(stock.series))
    return false
  return true
}

function assignTier(_stock: Stock, rank: number): DepthTier {
  if (rank < 5) return 'D50'
  if (rank < 255) return 'D30'
  if (rank < 455) return 'D5'
  return 'Eligible'
}

export function applyFilters(
  stocks: Stock[],
  filters: FilterState
): FilteredStock[] {
  const passing = stocks.filter((s) => passesFilter(s, filters))
  const sorted = [...passing].sort(
    (a, b) => b.avgTurnover20d - a.avgTurnover20d
  )
  const rankMap = new Map<string, number>()
  sorted.forEach((s, i) => rankMap.set(s.isin, i))

  return stocks.map((s) => {
    const pass = passesFilter(s, filters)
    const rank = rankMap.get(s.isin)
    return {
      ...s,
      pass,
      tier: pass && rank !== undefined ? assignTier(s, rank) : 'none',
    }
  })
}

export function computeFilterStages(
  stocks: Stock[],
  filters: FilterState
): FilterStageResult[] {
  const stages: FilterStageResult[] = []
  let remaining = [...stocks]

  // Series filter
  if (filters.series.length > 0) {
    const pass = remaining.filter((s) => filters.series.includes(s.series))
    stages.push({
      stageName: 'Series',
      inputCount: remaining.length,
      passCount: pass.length,
      failCount: remaining.length - pass.length,
    })
    remaining = pass
  }

  // Price filter
  {
    const pass = remaining.filter(
      (s) => s.lastPrice >= filters.priceMin && s.lastPrice <= filters.priceMax
    )
    stages.push({
      stageName: 'Price',
      inputCount: remaining.length,
      passCount: pass.length,
      failCount: remaining.length - pass.length,
    })
    remaining = pass
  }

  // Volume filter
  {
    const pass = remaining.filter(
      (s) => s.avgVolume20d >= filters.volumeMin
    )
    stages.push({
      stageName: 'Volume',
      inputCount: remaining.length,
      passCount: pass.length,
      failCount: remaining.length - pass.length,
    })
    remaining = pass
  }

  // Turnover filter
  {
    const pass = remaining.filter(
      (s) => s.avgTurnover20d >= filters.turnoverMin
    )
    stages.push({
      stageName: 'Turnover',
      inputCount: remaining.length,
      passCount: pass.length,
      failCount: remaining.length - pass.length,
    })
    remaining = pass
  }

  // Traded days
  {
    const pass = remaining.filter(
      (s) => s.tradedDays >= filters.minTradedDays
    )
    stages.push({
      stageName: 'Traded Days',
      inputCount: remaining.length,
      passCount: pass.length,
      failCount: remaining.length - pass.length,
    })
    remaining = pass
  }

  return stages
}
