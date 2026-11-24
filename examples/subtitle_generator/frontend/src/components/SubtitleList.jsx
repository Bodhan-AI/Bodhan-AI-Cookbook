import { useEffect, useState } from 'react'
import { Check, Copy, Download, LocateFixed, Pencil, Play } from 'lucide-react'
import { downloadText, formatTime } from '../format.js'
import LineEditor from './LineEditor.jsx'
import useLineFollow from '../useLineFollow.js'

// Subtitle lines with Original/Translated tabs, click-to-seek, editing, copy and SRT downloads.
// `tracks` is [{ id, label, language, code, segments, srt }]: the original first, then the translation if there is one.
// `onEdit(trackId, index, text)` saves a corrected line and resolves when saved (rejects with a readable error).
export default function SubtitleList({ tracks, activeTrack, onTrack, activeIndex, onSeek, onEdit }) {
  const [copied, setCopied] = useState(false)
  const [editing, setEditing] = useState(null) // index of the line being edited
  const track = tracks.find((t) => t.id === activeTrack) ?? tracks[0]
  // Follow the line being spoken (not while a line is being edited), until the user scrolls the list themselves
  const { listProps, following, resume } = useLineFollow(activeIndex, { paused: editing !== null })

  // A different track or job: drop any unsaved edit, and follow the video again
  useEffect(() => {
    setEditing(null)
    resume()
  }, [track.id, track.segments, resume])

  function seek(seconds) {
    resume() // jumping to a line means following the video from there
    onSeek(seconds)
  }

  function copy() {
    navigator.clipboard.writeText(track.srt).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="results-wrapper">
      <div className="tabs-header">
        {tracks.map((t) => (
          <button key={t.id} className={`tab-btn ${t.id === track.id ? 'active' : ''}`} onClick={() => onTrack(t.id)}>
            {t.label} · {t.language}
          </button>
        ))}
        <span
          className="timing-badge"
          title="Where speech starts and stops is measured; indic-transcribe returns no timestamps, so lines within a stretch of speech are placed by length."
        >
          Approximate timing
        </span>
        <button className="copy-btn" onClick={copy} title="Copy this SRT to the clipboard">
          {copied ? <Check size={14} className="copied-icon" /> : <Copy size={14} />}
          <span>{copied ? 'Copied' : 'Copy SRT'}</span>
        </button>
      </div>

      {/* tabIndex: the list scrolls with the keyboard too */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
      <div className="cues-scroll-area" tabIndex={0} aria-label="Subtitle lines" {...listProps}>
        {track.segments.map((cue, i) => (
          <div key={i} className={`cue-item ${i === activeIndex ? 'active' : ''}`}>
            {editing === i ? (
              <div className="cue-edit">
                <span className="cue-timestamp">{formatTime(cue.start)}</span>
                <LineEditor
                  text={cue.text}
                  label={`Edit the line at ${formatTime(cue.start)}`}
                  onSave={(text) => onEdit(track.id, i, text)}
                  onDone={() => setEditing(null)}
                />
              </div>
            ) : (
              <>
                <button className="cue-seek" title="Jump to this line" onClick={() => seek(cue.start)}>
                  <span className="cue-timestamp">
                    <Play size={10} className="cue-play-icon" />
                    <span>{formatTime(cue.start)}</span>
                  </span>
                  <span className="cue-content">{cue.text}</span>
                </button>
                {onEdit && (
                  <button
                    className="cue-edit-btn"
                    title="Edit this line"
                    aria-label={`Edit the line at ${formatTime(cue.start)}`}
                    onClick={() => setEditing(i)}
                  >
                    <Pencil size={14} />
                  </button>
                )}
              </>
            )}
          </div>
        ))}
        {!following && activeIndex >= 0 && (
          <button className="follow-btn" onClick={resume} title="Scroll back to the line being spoken and follow the video again">
            <LocateFixed size={14} />
            <span>Back to current line</span>
          </button>
        )}
      </div>

      <div className="download-actions">
        {tracks.map((t) => (
          <button
            key={t.id}
            className={`action-download-btn ${t.id === track.id ? 'primary' : 'secondary'}`}
            onClick={() => downloadText(`subtitles-${t.code}.srt`, t.srt)}
          >
            <Download size={16} />
            <span>Download {t.label.toLowerCase()} SRT</span>
          </button>
        ))}
      </div>
    </div>
  )
}
