import type { SignalType } from '@/types/contracts'

const priorities: Record<SignalType, number> = {
  B: 3,
  A: 2,
  C: 1,
}

export function resolveSignalPriority(signals: SignalType[]) {
  if (signals.length === 0) {
    return { primary: undefined, secondary: [] as SignalType[] }
  }

  const sorted = [...signals].sort((a, b) => priorities[b] - priorities[a])
  return {
    primary: sorted[0],
    secondary: sorted.slice(1),
  }
}

