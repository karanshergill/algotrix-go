export const RESOLUTIONS = ['1d', '1m', '5s'] as const

export type Resolution = (typeof RESOLUTIONS)[number]

export const RESOLUTION_LABELS: Record<Resolution, string> = {
  '1d': '1D',
  '1m': '1M',
  '5s': '5S',
}
