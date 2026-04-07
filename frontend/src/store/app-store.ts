import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import type { Locale } from '../lib/translations'

export interface RetrievalSettings {
  queryMode: 'hybrid' | 'graph' | 'vector'
  topK: number
  chunkTopK: number
  historyTurns: number
  userPrompt: string
  rerank: boolean
}

interface AppState {
  theme: 'light' | 'dark'
  locale: Locale
  token: string
  selectedStandardId: string | null
  retrieval: RetrievalSettings
  setTheme: (theme: AppState['theme']) => void
  setLocale: (locale: Locale) => void
  setToken: (token: string) => void
  setSelectedStandardId: (standardId: string | null) => void
  patchRetrieval: (payload: Partial<RetrievalSettings>) => void
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      theme: 'light',
      locale: 'zh-CN',
      token: '',
      selectedStandardId: null,
      retrieval: {
        queryMode: 'hybrid',
        topK: 8,
        chunkTopK: 12,
        historyTurns: 4,
        userPrompt: '',
        rerank: true,
      },
      setTheme: (theme) => set({ theme }),
      setLocale: (locale) => set({ locale }),
      setToken: (token) => set({ token }),
      setSelectedStandardId: (selectedStandardId) => set({ selectedStandardId }),
      patchRetrieval: (payload) =>
        set((state) => ({
          retrieval: {
            ...state.retrieval,
            ...payload,
          },
        })),
    }),
    {
      name: 'kg-agent-hhu-webui',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        theme: state.theme,
        locale: state.locale,
        token: state.token,
        selectedStandardId: state.selectedStandardId,
        retrieval: state.retrieval,
      }),
    },
  ),
)
