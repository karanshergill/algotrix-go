export type BacktestRun = {
  id: number
  type: string
  name: string
  config: {
    top_n: number
    step: number
    min_mcap?: number
    max_mcap?: number
    lookback?: number
    madtv_floor?: number
    min_score?: number
    weights?: Record<string, number>
  }
  status: 'running' | 'completed' | 'failed'
  summary: Record<string, HorizonSummary> | null
  build_dates_tested: number | null
  started_at: string
  completed_at: string | null
  created_by: string | null
}

export type HorizonSummary = {
  avg_max_opp: number
  avg_oc_ret: number
  avg_range: number
  avg_hit_rate: number
  edge_max_opp: number
  edge_range: number
  win_count: number
  total_count: number
}

export type Pick = {
  symbol: string
  isin: string
  rank: number
  score: number
  open_price: number
  high_price: number
  low_price: number
  close_price: number
  max_opp: number
  oc_return: number
}

export type DateResult = {
  id: number
  build_date: string
  horizon: number
  metrics: {
    max_opp: number
    oc_ret: number
    range: number
    hit_rate: number
  }
  benchmark: {
    nifty_max_opp: number
    nifty_range: number
  }
  picks: Pick[]
}

export type BacktestRunDetail = BacktestRun & {
  date_results: DateResult[]
}
