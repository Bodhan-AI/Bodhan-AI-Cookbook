import { useEffect, useState } from 'react'
import { Moon, Sun } from 'lucide-react'
// The Bodhan mark from console.bodhan.ai: black outline for light mode, white for dark
import markDark from '../assets/bodhan-mark-dark.svg'
import markLight from '../assets/bodhan-mark-light.svg'

// The theme the user picked with the button; same key as in index.html
const THEME_KEY = 'theme-choice'

function chosenTheme() {
  try {
    const theme = localStorage.getItem(THEME_KEY)
    return theme === 'light' || theme === 'dark' ? theme : null
  } catch {
    return null
  }
}

// index.html sets data-theme before the first paint: the user's choice, else the system's. Until the user
// picks one, the page follows the system theme, also when it changes while the page is open.
function useTheme() {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || 'light')
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  useEffect(() => {
    const system = window.matchMedia('(prefers-color-scheme: dark)')
    const follow = (e) => {
      if (!chosenTheme()) setTheme(e.matches ? 'dark' : 'light')
    }
    system.addEventListener('change', follow)
    return () => system.removeEventListener('change', follow)
  }, [])
  function toggle() {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    try {
      localStorage.setItem(THEME_KEY, next)
    } catch {
      // Storage blocked: the theme still applies for this visit
    }
  }
  return [theme, toggle]
}

export default function Header() {
  const [theme, toggleTheme] = useTheme()
  const label = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'

  return (
    <header className="topbar">
      <div className="brand">
        <span className="bodhan-lockup" role="img" aria-label="Bodhan.AI">
          <img className="mark-light" src={markLight} alt="" width="33" height="38" />
          <img className="mark-dark" src={markDark} alt="" width="33" height="38" />
        </span>
        <span className="brand-name">Subtitle Generator</span>
        <span className="tag-pill">v0</span>
      </div>
      <div className="topbar-meta">
        <span className="brand-subtitle">Bodhan AI Cookbook</span>
        <button className="theme-toggle" onClick={toggleTheme} title={label} aria-label={label}>
          {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
        </button>
      </div>
    </header>
  )
}
