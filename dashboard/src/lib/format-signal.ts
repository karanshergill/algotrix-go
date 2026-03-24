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

/** Screener badge color classes (outline style matching signal type badges) */
const SCREENER_COLORS: Record<string, string> = {
  early_momentum: 'text-amber-500 border-amber-500/30 bg-amber-500/10',
  'Early Momentum': 'text-amber-500 border-amber-500/30 bg-amber-500/10',
  sniper: 'text-rose-500 border-rose-500/30 bg-rose-500/10',
  Sniper: 'text-rose-500 border-rose-500/30 bg-rose-500/10',
  trident: 'text-violet-500 border-violet-500/30 bg-violet-500/10',
  Trident: 'text-violet-500 border-violet-500/30 bg-violet-500/10',
  thin_momentum: 'text-cyan-500 border-cyan-500/30 bg-cyan-500/10',
  'Thin Momentum': 'text-cyan-500 border-cyan-500/30 bg-cyan-500/10',
  two_session_high_breakout: 'text-blue-500 border-blue-500/30 bg-blue-500/10',
  Breakout: 'text-blue-500 border-blue-500/30 bg-blue-500/10',
}

export function screenerColorClass(raw: string): string {
  return SCREENER_COLORS[raw] ?? 'text-teal-500 border-teal-500/30 bg-teal-500/10'
}
