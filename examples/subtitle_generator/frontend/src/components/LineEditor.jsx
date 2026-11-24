import { useEffect, useRef, useState } from 'react'

// Edit box for one subtitle line. Enter saves, Esc cancels. Line breaks are joined into one line when saved.
// `onSave(text)` resolves when saved or rejects with a readable error; `onDone()` closes the box.
export default function LineEditor({ text, label, onSave, onDone }) {
  const [value, setValue] = useState(text)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef(null)

  // The editor opens because the user asked for it, so move the focus (and caret) into it
  useEffect(() => {
    const input = inputRef.current
    input?.focus()
    input?.setSelectionRange(input.value.length, input.value.length)
  }, [])

  async function save() {
    const trimmed = value.trim()
    if (!trimmed) return setError("A subtitle line can't be empty.")
    if (trimmed === text) return onDone()
    setSaving(true)
    setError('')
    try {
      await onSave(trimmed)
      onDone()
    } catch (e) {
      setSaving(false)
      setError(e.message)
    }
  }

  return (
    <form
      className="line-editor"
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      <textarea
        ref={inputRef}
        rows={2}
        maxLength={500}
        value={value}
        disabled={saving}
        aria-label={label}
        onChange={(e) => {
          setValue(e.target.value)
          setError('')
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            save()
          } else if (e.key === 'Escape') {
            e.preventDefault()
            onDone() // in real fullscreen the browser also leaves fullscreen on Esc; that can't be prevented
          }
        }}
      />
      {error && <p className="line-editor-error">{error}</p>}
      <div className="line-editor-actions">
        <button type="submit" className="line-editor-save" disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="line-editor-cancel" onClick={onDone} disabled={saving}>
          Cancel
        </button>
        <span className="line-editor-hint">Enter to save · Esc to cancel</span>
      </div>
    </form>
  )
}
