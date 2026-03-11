import { FeedControl } from '@/features/feed/feed-control'
import { TokenStatus } from '@/features/auth/token-status'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'

export function HeaderToolbar() {
  return (
    <div className='ml-auto flex items-center gap-2'>
      <FeedControl />
      <TokenStatus />
      <ThemeSwitch />
      <ProfileDropdown />
    </div>
  )
}
