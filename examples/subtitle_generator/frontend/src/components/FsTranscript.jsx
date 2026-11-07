import { useEffect, useState } from 'react'
import { LocateFixed, PanelRightClose, Pencil } from 'lucide-react'
import { formatTime } from '../format.js'
import LineEditor from './LineEditor.jsx'
import useLineFollow from '../useLineFollow.js'

// The transcript panel on the right of a fullscreen player (VideoPlayer and YouTubePlayer). Lines jump to their
// time with onSeek; with onEdit(index, text) they can be corrected (onStartEdit lets the player pause first).
export default function FsTranscript({ cues, cueIndex, trackLabel, onSeek, onEdit, onStartEdit, onClose }) {
  const [editing, setEditing] = useState(null) // index of the line being edited
  // Keep the line being spoken in the middle (not while a line is being edited), until the user scrolls themselves
  const { listProps, following, resume } = useLineFollow(cueIndex, { block: 'center', paused: editing !== null })

  // Another track: drop any unsaved edit, and follow the video again
  useEffect(() => {
    setEditing(null)
    resume()
  }, [cues, resume])

  return (
    <aside className="fs-transcript" aria-label="Transcript">
      <div className="fs-transcript-head">
        <div>
          <strong>Transcript</strong>
          {trackLabel && <span>{trackLabel}</span>}
        </div>
        <button className="ctrl-btn" onClick={onClose} title="Hide transcript (T)" aria-label="Hide transcript">
          <PanelRightClose size={17} />
        </button>
      </div>
      {cues.length ? (
        <ol className="fs-transcript-lines" {...listProps}>
          {cues.map((c, i) => (
            <li key={i} className={`fs-row ${i === cueIndex ? 'active' : ''}`}>
              {editing === i ? (
                <div className="fs-line fs-line-editing">
                  <span className="fs-line-time">{formatTime(c.start)}</span>
                  <LineEditor
                    text={c.text}
                    label={`Edit the line at ${formatTime(c.start)}`}
                    onSave={(text) => onEdit(i, text)}
                    onDone={() => setEditing(null)}
                  />
                </div>
              ) : (
                <>
                  <button
                    className="fs-line"
                    onClick={() => {
                      resume()
                      onSeek(c.start)
                    }}
                    title="Jump to this line"
                  >
                    <span className="fs-line-time">{formatTime(c.start)}</span>
                    <span>{c.text}</span>
                  </button>
                  {onEdit && (
                    <button
                      className="fs-edit-btn"
                      onClick={() => {
                        onStartEdit?.() // so the line being corrected stays on screen
                        setEditing(i)
                      }}
                      title="Edit this line"
                      aria-label={`Edit the line at ${formatTime(c.start)}`}
                    >
                      <Pencil size={14} />
                    </button>
                  )}
                </>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <p className="fs-transcript-empty">Subtitles will appear here once they are ready.</p>
      )}
      {!following && cueIndex >= 0 && (
        <button className="follow-btn" onClick={resume} title="Scroll back to the line being spoken and follow the video again">
          <LocateFixed size={14} />
          <span>Back to current line</span>
        </button>
      )}
    </aside>
  )
}
