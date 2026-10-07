import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type ThemeChoice = 'system' | 'light' | 'dark'
export type Resolved = 'light' | 'dark'

interface ThemeState {
  choice: ThemeChoice
  resolved: Resolved
  setChoice: (c: ThemeChoice) => void
}

const Ctx = createContext<ThemeState>({ choice: 'system', resolved: 'light', setChoice: () => {} })
const KEY = 'sti-theme'

function readChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}

function systemDark(): boolean {
  return typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(readChoice)
  const [sysDark, setSysDark] = useState(systemDark)

  useEffect(() => {
    const mq = matchMedia('(prefers-color-scheme: dark)')
    const on = () => setSysDark(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])

  const resolved: Resolved = choice === 'system' ? (sysDark ? 'dark' : 'light') : choice

  useEffect(() => {
    document.documentElement.dataset.theme = resolved
  }, [resolved])

  const value = useMemo<ThemeState>(
    () => ({
      choice,
      resolved,
      setChoice: (c) => {
        setChoiceState(c)
        try {
          localStorage.setItem(KEY, c)
        } catch {
          /* private mode: the choice lasts for this tab only */
        }
      },
    }),
    [choice, resolved],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useTheme = () => useContext(Ctx)
