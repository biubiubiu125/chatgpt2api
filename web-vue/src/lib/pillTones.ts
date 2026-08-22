export type PillTone = 'muted' | 'success' | 'warning' | 'danger' | 'info'
export type PillVariant = 'soft' | 'outline' | 'solid'

const pillToneClassMap: Record<PillTone, Record<PillVariant, string>> = {
  muted: {
    soft: '!border-foreground/20 !bg-muted/40 !text-foreground/75',
    outline: '!border-border !bg-background/70 !text-muted-foreground',
    solid: '!border-foreground/10 !bg-foreground !text-background',
  },
  success: {
    soft: '!border-emerald-500/45 !bg-emerald-500/10 !text-emerald-700 dark:!text-emerald-300',
    outline: '!border-emerald-500/45 !bg-transparent !text-emerald-500',
    solid: '!border-emerald-500 !bg-emerald-500 !text-white',
  },
  warning: {
    soft: '!border-amber-500/45 !bg-amber-500/10 !text-amber-700 dark:!text-amber-300',
    outline: '!border-amber-500/45 !bg-transparent !text-amber-500',
    solid: '!border-amber-500 !bg-amber-500 !text-white',
  },
  danger: {
    soft: '!border-rose-500/45 !bg-rose-500/10 !text-rose-700 dark:!text-rose-300',
    outline: '!border-rose-500/45 !bg-transparent !text-rose-500',
    solid: '!border-rose-500 !bg-rose-500 !text-white',
  },
  info: {
    soft: '!border-cyan-500/45 !bg-cyan-500/10 !text-cyan-700 dark:!text-cyan-300',
    outline: '!border-cyan-500/45 !bg-transparent !text-cyan-500',
    solid: '!border-cyan-500 !bg-cyan-500 !text-white',
  },
}

export const PILL_TONE_CLASS = {
  success: pillToneClassMap.success.soft,
  warning: pillToneClassMap.warning.soft,
  danger: pillToneClassMap.danger.soft,
  info: pillToneClassMap.info.soft,
  muted: pillToneClassMap.muted.soft,
  neutral: pillToneClassMap.muted.soft,
} as const

export function pillToneClass(tone: PillTone = 'muted', variant: PillVariant = 'soft') {
  return pillToneClassMap[tone][variant]
}
