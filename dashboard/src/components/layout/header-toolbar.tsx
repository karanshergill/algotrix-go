import { useState } from 'react'
import { Bell, BellOff } from 'lucide-react'
import { FeedControl } from '@/features/feed/feed-control'
import { IndexTicker } from '@/features/indices/index-ticker'
import { IndexTickerRotator } from '@/features/indices/index-ticker-rotator'
import { useIndexQuotes } from '@/features/indices/use-index-quotes'
import { TokenStatus } from '@/features/auth/token-status'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { isAlertsEnabled, setAlertsEnabled } from '@/hooks/use-signal-alerts'

function isMarketOpen(): boolean {
  const now = new Date()
  const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
  const day = ist.getDay()
  if (day === 0 || day === 6) return false
  const t = ist.getHours() * 60 + ist.getMinutes()
  return t >= 9 * 60 + 15 && t <= 15 * 60 + 30
}

// Pinned ticker — always visible on the left
const HEADER_PINNED = 'NSE:NIFTY50-INDEX'

// Rotating tickers — cycle through on the right slot
const HEADER_ROTATING = [
  'NSE:NIFTYBANK-INDEX',
  'NSE:FINNIFTY-INDEX',
  'NSE:MIDCPNIFTY-INDEX',
  'NSE:NIFTYIT-INDEX',
  'NSE:NIFTYPHARMA-INDEX',
  'NSE:NIFTYMETAL-INDEX',
  'NSE:NIFTYAUTO-INDEX',
  'NSE:NIFTYREALTY-INDEX',
]

const ALL_SYMBOLS = [HEADER_PINNED, ...HEADER_ROTATING]

export function HeaderToolbar() {
  const { data: quotes = [] } = useIndexQuotes(ALL_SYMBOLS)
  const marketOpen = isMarketOpen()
  const [alertsOn, setAlertsOn] = useState(isAlertsEnabled)

  const pinnedData = quotes.find((q) => q.symbol === HEADER_PINNED)

  const toggleAlerts = () => {
    const next = !alertsOn
    setAlertsOn(next)
    setAlertsEnabled(next)

    // First click: request Notification permission + prime Audio (browser autoplay policy).
    if (next) {
      if (Notification.permission === 'default') {
        Notification.requestPermission()
      }
      // Prime audio context with user gesture.
      const a = new Audio('/alert-buy.mp3')
      a.volume = 0
      a.play().then(() => a.pause()).catch(() => {})
    }
  }

  return (
    <div className='ml-auto flex items-center gap-3'>
      {/* Index tickers */}
      <div className='hidden md:flex items-center gap-3'>
        <IndexTicker symbol={HEADER_PINNED} data={pinnedData} compact />
        <span className='text-border select-none'>|</span>
        {/* w-[15.5rem] = exact sum of ticker slots (6.5+5+4rem) — no overflow clipping */}
        <div className='w-[15.5rem]'>
          <IndexTickerRotator symbols={HEADER_ROTATING} quotes={quotes} intervalMs={4000} active={marketOpen} />
        </div>
      </div>

      <Separator orientation='vertical' className='hidden md:block h-5' />

      {/* Controls */}
      <FeedControl />
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant='ghost' size='icon' onClick={toggleAlerts} className='relative'>
            {alertsOn ? (
              <>
                <Bell className='h-4 w-4 text-emerald-500 animate-[chime_1.5s_ease-in-out_infinite]' />
                <span className='absolute top-1 right-1 h-2 w-2 rounded-full bg-emerald-500 animate-pulse' />
              </>
            ) : (
              <BellOff className='h-4 w-4 opacity-50' />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{alertsOn ? 'Signal Alerts: ON' : 'Signal Alerts: OFF'}</TooltipContent>
      </Tooltip>
      <TokenStatus />
      <ThemeSwitch />
      <ProfileDropdown />
    </div>
  )
}
