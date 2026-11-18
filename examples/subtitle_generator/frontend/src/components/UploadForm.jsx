import { useRef, useState } from 'react'
import { AlertTriangle, Captions, FileVideo, Languages, Link2, LoaderCircle, Upload, X } from 'lucide-react'
import { LANGUAGES, TRANSLATION_LANGUAGES } from '../languages.js'
import { VIDEO_EXTENSIONS, isLink, isSiteLink, isYouTubeLink } from '../links.js'

// Left panel: pick a video (a file, or a link) and languages, then start generation.
// maxMb is the backend's upload limit (MAX_UPLOAD_MB); directLinks: whether the backend takes direct links to video
// files (DIRECT_VIDEO_LINKS); both read from /api/health.
export default function UploadForm({ file, onFile, onError, link, onLink, directLinks, maxMb, settings, onSettings, busy, onGenerate }) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef(null)
  const { language, translate, target, source } = settings
  const linkMode = directLinks && source === 'link'
  const linkProblem = linkIssue(link)
  const ready = linkMode ? isLink(link) && !linkProblem : Boolean(file)

  function choose(f) {
    if (!f) return
    const isVideo = f.type.startsWith('video/') || VIDEO_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext))
    if (!isVideo) return onError('Please choose a video file (MP4, MOV, MKV, WEBM, AVI, M4V).')
    if (f.size > maxMb * 1024 * 1024) return onError(`The upload limit is ${maxMb} MB.`)
    onFile(f)
  }

  function setLanguage(code) {
    // Never translate into the language being spoken
    const nextTarget = code === target ? TRANSLATION_LANGUAGES.find(([c]) => c !== code)[0] : target
    onSettings({ ...settings, language: code, target: nextTarget })
  }

  return (
    <section className="panel" aria-labelledby="step-1-title">
      <div className="panel-heading">
        <span className="step-badge">1</span>
        <div>
          <h2 id="step-1-title">Video and languages</h2>
          <p>{directLinks ? 'Choose a video file or paste a link' : 'Choose a file'}, and tell us what is spoken in it.</p>
        </div>
      </div>

      {/* Shown only when links are on (DIRECT_VIDEO_LINKS in .env) */}
      {directLinks && (
        <div className="source-switch" role="group" aria-label="Where the video comes from">
          <button
            className={!linkMode ? 'active' : ''}
            aria-pressed={!linkMode}
            onClick={() => onSettings({ ...settings, source: 'file' })}
          >
            <Upload size={15} />
            <span>Upload a file</span>
          </button>
          <button className={linkMode ? 'active' : ''} aria-pressed={linkMode} onClick={() => onSettings({ ...settings, source: 'link' })}>
            <Link2 size={15} />
            <span>Paste a link</span>
          </button>
        </div>
      )}

      {linkMode ? (
        <div className="link-source">
          <div className="field-group">
            <label htmlFor="video-link">Video link</label>
            <div className="link-input">
              <Link2 size={16} className="link-input-icon" />
              <input
                id="video-link"
                type="url"
                inputMode="url"
                placeholder="https://example.com/talk.mp4"
                value={link}
                onChange={(e) => onLink(e.target.value)}
                aria-describedby="video-link-help"
              />
              {link && (
                <button className="remove-btn" title="Clear the link" aria-label="Clear the link" onClick={() => onLink('')}>
                  <X size={16} />
                </button>
              )}
            </div>
            <small id="video-link-help" className="field-help">
              {linkHelp(link, maxMb)}
            </small>
          </div>

          {linkProblem && (
            <div className="notice-banner warning" role="note">
              <AlertTriangle size={18} />
              <span>{linkProblem}</span>
            </div>
          )}
        </div>
      ) : (
        <div
          role="button"
          tabIndex={0}
          className={`dropzone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              inputRef.current?.click()
            }
          }}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            choose(e.dataTransfer.files?.[0])
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept={`video/*,${VIDEO_EXTENSIONS.join(',')}`}
            hidden
            onChange={(e) => choose(e.target.files?.[0])}
          />
          {file ? (
            <div className="file-info-box">
              <span className="upload-icon success">
                <FileVideo size={24} />
              </span>
              <strong className="filename">{file.name}</strong>
              <span className="file-size-tag">{(file.size / (1024 * 1024)).toFixed(1)} MB</span>
              <button
                className="remove-btn"
                title="Remove video"
                aria-label="Remove video"
                onClick={(e) => {
                  e.stopPropagation()
                  onFile(null)
                }}
              >
                <X size={16} />
              </button>
            </div>
          ) : (
            <div className="empty-drop">
              <span className="upload-icon">
                <Upload size={24} />
              </span>
              <strong>Drop a video here, or click to browse</strong>
              <span className="muted-text">MP4, MOV, MKV, WEBM, AVI, M4V</span>
              <span className="limit-tag">Up to {maxMb} MB</span>
            </div>
          )}
        </div>
      )}

      <div className="form-controls">
        <div className="field-group">
          <label htmlFor="source-language">Spoken language in video</label>
          <select id="source-language" value={language} onChange={(e) => setLanguage(e.target.value)}>
            {LANGUAGES.map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <label className="toggle-wrapper" htmlFor="translate-toggle">
          <div className="toggle-copy">
            <Languages size={18} className="toggle-icon" />
            <div>
              <strong>Translate subtitles</strong>
              <small>Also create a subtitle file in another language</small>
            </div>
          </div>
          <input
            id="translate-toggle"
            type="checkbox"
            checked={translate}
            onChange={(e) => onSettings({ ...settings, translate: e.target.checked })}
          />
          <span className="toggle-slider" />
        </label>

        {translate && (
          <div className="field-group">
            <label htmlFor="target-language">Translate subtitles into</label>
            <select id="target-language" value={target} onChange={(e) => onSettings({ ...settings, target: e.target.value })}>
              {TRANSLATION_LANGUAGES.filter(([code]) => code !== language).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        )}

        <button className="primary-button" disabled={busy || !ready} onClick={onGenerate}>
          {busy ? <LoaderCircle className="spin" size={18} /> : <Captions size={18} />}
          <span>{busy ? 'Working on it… longer videos take a few minutes' : 'Generate Subtitles'}</span>
        </button>
        <p className="security-notice">
          Videos are deleted from the server when the job ends. Only the subtitle text{linkMode ? ' and the link' : ''} is kept, in your
          history.
        </p>
      </div>
    </section>
  )
}

// The hint under the link box: what works, then what applies to the link pasted
function linkHelp(link, maxMb) {
  if (link && !isLink(link)) return 'Paste a full link that starts with http:// or https://.'
  if (link && !isSiteLink(link))
    return `Link to a video file: it is downloaded, then deleted when the job ends. Up to ${maxMb} MB and 15 minutes.`
  return `Paste a direct link to a video file (ending in .mp4, .mov…, or a public cloud-storage file link). Up to ${maxMb} MB and 15 minutes.`
}

// Why a pasted link can't be used, or ''. Same messages as the backend (download.check_link).
function linkIssue(link) {
  if (!isLink(link)) return ''
  if (isYouTubeLink(link))
    return 'YouTube links aren’t supported. Upload the video file instead: for your own videos, download it from YouTube Studio.'
  if (isSiteLink(link))
    return 'Links to video sites aren’t supported. Paste a link that points straight at the video file, or upload the file.'
  return ''
}
