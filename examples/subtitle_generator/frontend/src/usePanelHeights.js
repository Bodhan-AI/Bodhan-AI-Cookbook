import { useEffect } from 'react'

const SIDE_BY_SIDE = '(min-width: 901px)' // matches the two-column layout in styles.css

const px = (el, property) => parseFloat(getComputedStyle(el)[property]) || 0

// Side by side, keep the preview panel (second child of the workspace) as tall as the form panel (first child, which
// keeps its natural height: the workspace aligns panels to the top, so this never feeds back on itself):
// its subtitle list scrolls instead of stretching the page. It is taller only when the video, tabs, buttons and
// the list's minimum height need more room than that.
export default function usePanelHeights(workspaceRef) {
  useEffect(() => {
    const workspace = workspaceRef.current
    if (!workspace) return
    const [form, preview] = workspace.children
    const wide = window.matchMedia(SIDE_BY_SIDE)

    function update() {
      const list = preview.querySelector('.cues-scroll-area')
      if (!wide.matches || !list) {
        preview.style.removeProperty('height') // stacked, or nothing to scroll: natural height
        return
      }
      // Everything in the panel with the list at its minimum height, added up part by part (scrollHeight leaves out
      // the bottom padding when the overflow is inside a nested box). Flex items' margins don't collapse.
      const edges = (el) => px(el, 'paddingTop') + px(el, 'paddingBottom') + px(el, 'borderTopWidth') + px(el, 'borderBottomWidth')
      const margins = (el) => px(el, 'marginTop') + px(el, 'marginBottom')
      let needed = edges(preview)
      for (const child of preview.children) {
        if (!child.contains(list)) {
          needed += child.offsetHeight + margins(child)
          continue
        }
        needed += edges(child) + margins(child)
        for (const part of child.children) needed += (part === list ? px(list, 'minHeight') : part.offsetHeight) + margins(part)
      }
      let height = Math.ceil(Math.max(form.offsetHeight, needed))
      if (preview.style.height !== `${height}px`) preview.style.height = `${height}px`
      // Check the result: if the list's box still can't hold its parts (fonts or lines that settled after measuring),
      // grow by exactly what is missing, so the buttons keep the panel's bottom padding
      const box = list.parentElement
      const missing = box.scrollHeight - box.clientHeight
      if (missing > 0) {
        height += missing
        preview.style.height = `${height}px`
      }
    }

    // Re-measure when the form changes size, or anything in the preview does (the video loads and grows, a banner
    // wraps, the tabs or buttons change), including the parts inside the list's box
    const resized = new ResizeObserver(update)
    function watch() {
      resized.disconnect()
      const box = preview.querySelector('.cues-scroll-area')?.parentElement
      for (const element of [form, preview, ...preview.children, ...(box ? box.children : [])]) resized.observe(element)
      update()
    }
    // Results, banners and the progress bar come and go in the panel
    const changed = new MutationObserver(watch)
    changed.observe(preview, { childList: true, subtree: false })
    wide.addEventListener('change', update)
    document.fonts?.ready.then(update) // text can change height once web fonts arrive
    watch()
    return () => {
      resized.disconnect()
      changed.disconnect()
      wide.removeEventListener('change', update)
      preview.style.removeProperty('height')
    }
  }, [workspaceRef])
}
