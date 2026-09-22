import { useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'portal_theme'

function initialTheme(): Theme {
  // index.html already applied this synchronously (before first paint,
  // to avoid a flash of the wrong theme) -- read that back rather than
  // recomputing, so this hook's state matches what's actually on screen
  // from the very first render.
  const applied = document.documentElement.getAttribute('data-theme')
  return applied === 'light' ? 'light' : 'dark'
}

/** Theme state shared by every component that renders it (just the one
 * toggle button today) -- kept as a hook rather than context since
 * there's only ever one consumer at a time; promote to context if a
 * second one needs to read it. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    window.localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  function toggle() {
    setTheme((t) => (t === 'dark' ? 'light' : 'dark'))
  }

  return [theme, toggle]
}
