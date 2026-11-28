import { useCallback, useEffect, useRef, useState } from 'react'

// Keeps the line being spoken in view in a scrolling list of subtitle lines, until the user scrolls the list
// themselves: then it stops following, so they can read other lines while the video plays. `resume()` follows again
// (the list shows a "Back to current line" button; clicking a line to jump there also resumes).
// Only the list scrolls, never the page around it. The list's rows mark the line being spoken with the class `active`.
// `block`: 'nearest' scrolls only when the line leaves the view; 'center' keeps it in the middle.
export default function useLineFollow(activeIndex, { block = 'nearest', paused = false } = {}) {
  const listRef = useRef(null)
  const [following, setFollowing] = useState(true)

  useEffect(() => {
    const list = listRef.current
    const line = list?.querySelector('.active')
    if (!following || paused || !line) return
    const listBox = list.getBoundingClientRect()
    const lineBox = line.getBoundingClientRect()
    const above = lineBox.top < listBox.top
    const below = lineBox.bottom > listBox.bottom
    if (block === 'nearest' && !above && !below) return
    const offset = lineBox.top - listBox.top
    const top =
      block === 'center' ? offset - (list.clientHeight - lineBox.height) / 2 : above ? offset : offset - list.clientHeight + lineBox.height
    list.scrollTo({ top: list.scrollTop + top, behavior: 'smooth' })
  }, [activeIndex, following, paused, block])

  const stop = useCallback(() => setFollowing(false), [])
  const resume = useCallback(() => setFollowing(true), [])

  // The user scrolling the list: the wheel, a touch drag, the keyboard on the list, or its scrollbar
  // (a press on the list itself, not on a line). Programmatic scrolling fires none of these.
  const listProps = {
    ref: listRef,
    onWheel: stop,
    onTouchMove: stop,
    onKeyDown: (e) => {
      if (e.target === e.currentTarget && ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(e.key)) stop()
    },
    onPointerDown: (e) => {
      if (e.target === e.currentTarget) stop()
    },
  }

  return { listProps, following, resume }
}
