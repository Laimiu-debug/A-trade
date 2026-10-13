import { create } from 'zustand'
import type { AIParameterProposalChange, AIParameterProposalResponse } from '@/types/contracts'

type AIParameterApplyState = {
  pendingProposal: AIParameterProposalResponse | null
  lastApplied: AIParameterProposalChange[] | null
  setPendingProposal: (proposal: AIParameterProposalResponse | null) => void
  notifyApplied: (changes: AIParameterProposalChange[]) => void
  clearLastApplied: () => void
}

export const useAIParameterApplyStore = create<AIParameterApplyState>((set) => ({
  pendingProposal: null,
  lastApplied: null,
  setPendingProposal: (proposal) => set({ pendingProposal: proposal }),
  notifyApplied: (changes) => set({ lastApplied: changes, pendingProposal: null }),
  clearLastApplied: () => set({ lastApplied: null }),
}))

export function applyPageFormChange(
  target: string,
  newValue: unknown,
  handlers: Record<string, (value: unknown) => void>,
) {
  const handler = handlers[target]
  if (handler) handler(newValue)
}
