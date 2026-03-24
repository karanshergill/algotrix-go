export type Signal = {
  id: number
  session_date: string
  triggered_at: string
  screener_name: string
  security_id: number
  trading_symbol: string
  signal_type: string
  trigger_price: number
  threshold_price: number
  ltp: number
  percent_above: number
  metadata: Record<string, unknown> | null
  trigger_values: Record<string, unknown> | null
}

export type SignalSummary = {
  screener_name: string
  count: number
}
