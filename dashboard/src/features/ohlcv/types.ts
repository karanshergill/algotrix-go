import { RESOLUTIONS, type Resolution } from './constants'

export type CoverageStatus = 'full' | 'partial' | 'missing'
export type OhlcvJobStatus = 'running' | 'completed' | 'failed'

export interface ResolutionCoverage {
  count: number
  status: CoverageStatus
}

export type DayOhlcvStatus = Record<Resolution, ResolutionCoverage>

export interface OhlcvStatusResponse {
  totalSymbols: number
  days: Record<string, DayOhlcvStatus>
}

export interface OhlcvSymbolRow {
  isin: string
  symbol: string
  hasData: boolean
  rowCount: number
}

export interface OhlcvSymbolsResponse {
  date: string
  resolution: Resolution
  symbols: OhlcvSymbolRow[]
}

export interface OhlcvFetchJob {
  jobId: string
  status: OhlcvJobStatus
  resolution: Resolution
  from: string
  to: string
  done: number
  total: number
  errors: number
  message?: string
  startedAt: string
  finishedAt?: string
}

export function createMissingDayStatus(): DayOhlcvStatus {
  return {
    [RESOLUTIONS[0]]: { count: 0, status: 'missing' },
    [RESOLUTIONS[1]]: { count: 0, status: 'missing' },
    [RESOLUTIONS[2]]: { count: 0, status: 'missing' },
  }
}

export function getCoveragePercent(count: number, total: number): number {
  if (total <= 0) {
    return 0
  }

  return Math.min(100, Math.round((count / total) * 100))
}
