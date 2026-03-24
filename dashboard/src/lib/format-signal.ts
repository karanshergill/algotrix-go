/** Map raw screener_name from DB to display label */
const SCREENER_LABELS: Record<string, string> = {
  early_momentum: 'Early Momentum',
  sniper: 'Sniper',
  trident: 'Trident',
  thin_momentum: 'Thin Momentum',
  two_session_high_breakout: 'Breakout',
}

export function formatScreenerName(raw: string): string {
  return SCREENER_LABELS[raw] ?? raw.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function formatSignalType(raw: string): string {
  return raw.toUpperCase()
}
