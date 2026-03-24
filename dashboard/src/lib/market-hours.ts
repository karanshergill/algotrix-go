const IST_OFFSET = 5.5 * 60 // minutes

function toIST(date: Date): Date {
  const utcMs = date.getTime() + date.getTimezoneOffset() * 60_000
  return new Date(utcMs + IST_OFFSET * 60_000)
}

export function isMarketOpen(): boolean {
  const now = toIST(new Date())
  const day = now.getDay()
  if (day === 0 || day === 6) return false // Sat/Sun

  const mins = now.getHours() * 60 + now.getMinutes()
  return mins >= 9 * 60 + 15 && mins < 15 * 60 + 30
}

export function getNextOpenLabel(): string {
  const now = toIST(new Date())
  const day = now.getDay()
  const mins = now.getHours() * 60 + now.getMinutes()

  // If before open on a weekday, opens today
  if (day >= 1 && day <= 5 && mins < 9 * 60 + 15) {
    return 'Opens 9:15 AM'
  }

  // Otherwise, next weekday
  const daysUntilMon =
    day === 5 ? 3 : day === 6 ? 2 : day === 0 ? 1 : 1
  const label = daysUntilMon === 1 ? 'tomorrow' : 'Mon'
  return `Opens ${label} 9:15 AM`
}

/** Returns today's date in IST as YYYY-MM-DD string. */
export function getISTDate(): string {
  const now = toIST(new Date())
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}
