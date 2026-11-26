import { useEffect, useRef, useState } from 'react'
import { Check } from 'lucide-react'

// A player-bar button that opens a menu above it (a drop-up: the bar sits at the bottom of the video).
// `options` is [{ value, label }]; picking one calls onChange(value) and closes the menu. Closes on Escape and on a
// click outside; arrow keys move between the choices.
export default function ControlMenu({ label, title, heading, options, value, onChange, className = '' }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const menuRef = useRef(null)

  useEffect(() => {
    if (!open) return
    // Start on the current choice, so Enter keeps it and the arrows move from there
    menuRef.current?.querySelector('[aria-checked="true"]')?.focus()
    const onPointer = (e) => {
      if (!rootRef.current?.contains(e.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointer)
    return () => document.removeEventListener('pointerdown', onPointer)
  }, [open])

  function onMenuKey(e) {
    const items = [...menuRef.current.querySelectorAll('[role="menuitemradio"]')]
    const at = items.indexOf(document.activeElement)
    const moves = { ArrowUp: at - 1, ArrowDown: at + 1, Home: 0, End: items.length - 1 }
    if (e.key === 'Escape') {
      setOpen(false)
      rootRef.current.querySelector('.ctrl-menu-btn')?.focus()
    } else if (e.key in moves) {
      items[(moves[e.key] + items.length) % items.length]?.focus()
    } else {
      return
    }
    // Handled here: not also by the player's own shortcuts (arrows seek, Space plays)
    e.preventDefault()
    e.stopPropagation()
  }

  return (
    <div className="ctrl-menu" ref={rootRef}>
      <button
        className={`ctrl-btn ctrl-menu-btn ${className} ${open ? 'open' : ''}`}
        onClick={() => setOpen((on) => !on)}
        title={title}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {label}
      </button>
      {open && (
        <div className="ctrl-menu-list" role="menu" tabIndex={-1} aria-label={heading} ref={menuRef} onKeyDown={onMenuKey}>
          <div className="ctrl-menu-heading">{heading}</div>
          {options.map((option) => (
            <button
              key={option.value}
              role="menuitemradio"
              aria-checked={option.value === value}
              className={`ctrl-menu-item ${option.value === value ? 'selected' : ''}`}
              onClick={() => {
                onChange(option.value)
                setOpen(false)
              }}
            >
              <Check size={14} className="ctrl-menu-check" />
              <span>{option.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
