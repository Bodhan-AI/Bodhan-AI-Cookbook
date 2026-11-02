import { useEffect, useImperativeHandle, useRef, useState } from 'react'
import {
  Captions,
  Clapperboard,
  Languages,
  Maximize,
  Minimize,
  PanelRight,
  PanelRightClose,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { formatTime } from '../format.js'
import ControlMenu from './ControlMenu.jsx'
import FsTranscript from './FsTranscript.jsx'

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]

// Plays a local `file`, or else a `videoUrl` (a direct link to a video file), with the line being spoken drawn over it.
// YouTube videos play in YouTubePlayer instead, which takes the same props.
// - `cues` / `trackLabel`: the lines shown; onActiveLine(index) reports the line being spoken; `ref.current.seekTo`.
// - Fullscreen: the transcript panel on the right, where onEdit(index, text) corrects a line; with a translation,
//   `trackOptions` ([{ value, label, short }]), `activeTrack` and onTrack(id) switch the subtitle language.
export default function VideoPlayer({ ref, file, videoUrl, cues, trackLabel, trackOptions, activeTrack, onTrack, onActiveLine, onEdit }) {
  const videoRef = useRef(null)
  const containerRef = useRef(null)
  const barRef = useRef(null)
  const [url, setUrl] = useState('')
  const [unplayable, setUnplayable] = useState(false) // the browser couldn't load the video link
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(1)
  const [muted, setMuted] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [showCaptions, setShowCaptions] = useState(true)
  const [fullscreen, setFullscreen] = useState(false)
  const [showTranscript, setShowTranscript] = useState(true)
  const [hover, setHover] = useState(null) // { time, x } while the pointer is over the progress bar

  // Play the chosen file from a local object URL (the browser never re-downloads it), or else the video link
  useEffect(() => {
    setPlaying(false)
    setTime(0)
    setDuration(0)
    setUnplayable(false)
    if (!file) return setUrl(videoUrl || '')
    const objectUrl = URL.createObjectURL(file)
    setUrl(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [file, videoUrl])

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  // Keyboard shortcuts, ignored while typing or while a button has focus
  useEffect(() => {
    function onKey(e) {
      const focused = document.activeElement
      if (!videoRef.current || focused?.closest?.('input, select, textarea')) return
      if (focused?.closest?.('button, [role="button"]') && (e.code === 'Space' || e.key === 'Enter')) return
      const actions = {
        Space: togglePlay,
        ArrowLeft: () => seek(videoRef.current.currentTime - 5),
        ArrowRight: () => seek(videoRef.current.currentTime + 5),
        KeyM: toggleMute,
        KeyC: () => setShowCaptions((on) => !on),
        KeyF: toggleFullscreen,
        KeyT: () => setShowTranscript((on) => !on),
      }
      if (actions[e.code]) {
        e.preventDefault()
        actions[e.code]()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  useImperativeHandle(ref, () => ({
    seekTo(seconds) {
      seek(seconds)
      videoRef.current?.play().catch(() => {})
    },
  }))

  function updateTime(seconds) {
    setTime(seconds)
  }

  function seek(seconds) {
    const video = videoRef.current
    if (!video) return
    video.currentTime = Math.max(0, Math.min(duration || 0, seconds))
    updateTime(video.currentTime)
  }

  function togglePlay() {
    const video = videoRef.current
    if (!video) return
    if (video.paused || video.ended) video.play().catch(() => {})
    else video.pause()
  }

  function changeVolume(value) {
    const v = Number(value)
    setVolume(v)
    setMuted(v === 0)
    videoRef.current.volume = v
    videoRef.current.muted = v === 0
  }

  function toggleMute() {
    const video = videoRef.current
    if (!video) return
    const unmute = muted
    video.muted = !unmute
    setMuted(!unmute)
    if (unmute && volume === 0) changeVolume(1)
  }

  function changeSpeed(next) {
    setSpeed(next)
    videoRef.current.playbackRate = next
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
    else containerRef.current?.requestFullscreen().catch(() => {})
  }

  const progress = duration ? Math.min(100, (time / duration) * 100) : 0
  const cueIndex = cues.findIndex((c) => time >= c.start && time <= c.end)

  // Tell the page only when the line being spoken changes
  useEffect(() => onActiveLine?.(cueIndex), [cueIndex, onActiveLine])
  const cue = cues[cueIndex]
  const transcriptOpen = fullscreen && showTranscript

  function pointerOnBar(e) {
    const rect = barRef.current.getBoundingClientRect()
    const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left))
    return { time: (x / rect.width) * duration, x }
  }

  if ((!file && !videoUrl) || unplayable) {
    return (
      <div className="video-container">
        <div className="video-empty-state">
          <span className="preview-glyph">
            <Clapperboard size={28} strokeWidth={1.5} />
          </span>
          <strong>{unplayable ? 'This link can’t be played here' : 'No video yet'}</strong>
          <span>
            {unplayable
              ? 'The site doesn’t let the browser play it here, or the link has expired. Subtitles still work.'
              : 'Your video will play here with its subtitles'}
          </span>
        </div>
      </div>
    )
  }

  return (
    <div ref={containerRef} className={`video-container ${fullscreen ? 'is-fullscreen' : ''}`}>
      <div className="player-main">
        {/* Clicking the picture is a mouse shortcut for the Play button (Space does the same from the keyboard) */}
        <div className="video-viewport" role="presentation" onClick={togglePlay}>
          {/* eslint-disable-next-line jsx-a11y/media-has-caption -- captions are the subtitle lines, drawn by the overlay below */}
          <video
            key={url}
            ref={videoRef}
            src={url || undefined}
            playsInline
            className="video-player"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onTimeUpdate={(e) => updateTime(e.target.currentTime)}
            onDurationChange={(e) => setDuration(e.target.duration || 0)}
            onError={() => !file && setUnplayable(true)}
          />
          {!playing && (
            <button
              className="center-play-button"
              onClick={(e) => {
                e.stopPropagation()
                togglePlay()
              }}
              aria-label="Play"
            >
              <Play size={26} className="center-play-icon" />
            </button>
          )}
          {showCaptions && cue && (
            <div className="custom-subtitle-overlay">
              <span className="custom-subtitle-text">{cue.text}</span>
            </div>
          )}
        </div>

        <div className="player-controls-bar">
          <div
            ref={barRef}
            className="progress-bar-container"
            role="slider"
            tabIndex={0}
            aria-label="Seek"
            aria-valuemin={0}
            aria-valuemax={Math.round(duration)}
            aria-valuenow={Math.round(time)}
            aria-valuetext={`${formatTime(time)} of ${formatTime(duration)}`}
            onKeyDown={(e) => {
              const to = { ArrowLeft: time - 5, ArrowDown: time - 5, ArrowRight: time + 5, ArrowUp: time + 5, Home: 0, End: duration }[
                e.key
              ]
              if (to === undefined) return
              e.preventDefault()
              e.stopPropagation() // handled here, not again by the player's own arrow-key shortcuts
              seek(to)
            }}
            onClick={(e) => duration && seek(pointerOnBar(e).time)}
            onMouseMove={(e) => duration && setHover(pointerOnBar(e))}
            onMouseLeave={() => setHover(null)}
          >
            <div className="progress-bar-track">
              <div className="progress-bar-fill" style={{ width: `${progress}%` }} />
              <div className="progress-bar-thumb" style={{ left: `${progress}%` }} />
            </div>
            {hover && (
              <div className="progress-hover-tooltip" style={{ left: hover.x }}>
                {formatTime(hover.time)}
              </div>
            )}
          </div>

          <div className="controls-row">
            <div className="controls-left">
              <button
                className="ctrl-btn play-pause-btn"
                onClick={togglePlay}
                title={playing ? 'Pause (Space)' : 'Play (Space)'}
                aria-label={playing ? 'Pause' : 'Play'}
              >
                {playing ? <Pause size={18} /> : <Play size={18} />}
              </button>
              <button className="ctrl-btn skip-btn" onClick={() => seek(time - 5)} title="Back 5 seconds (←)" aria-label="Back 5 seconds">
                <RotateCcw size={15} />
                <span className="skip-tag">5</span>
              </button>
              <button
                className="ctrl-btn skip-btn"
                onClick={() => seek(time + 5)}
                title="Forward 5 seconds (→)"
                aria-label="Forward 5 seconds"
              >
                <RotateCw size={15} />
                <span className="skip-tag">5</span>
              </button>
              <div className="volume-control-group">
                <button
                  className="ctrl-btn volume-btn"
                  onClick={toggleMute}
                  title={muted ? 'Unmute (M)' : 'Mute (M)'}
                  aria-label={muted ? 'Unmute' : 'Mute'}
                >
                  {muted || volume === 0 ? <VolumeX size={17} /> : <Volume2 size={17} />}
                </button>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={muted ? 0 : volume}
                  onChange={(e) => changeVolume(e.target.value)}
                  className="volume-slider"
                  aria-label="Volume"
                />
              </div>
              <div className="time-display">
                <span className="current-time">{formatTime(time)}</span>
                <span>/</span>
                <span>{formatTime(duration)}</span>
              </div>
            </div>

            <div className="controls-right">
              <ControlMenu
                className="speed-btn"
                label={`${speed}x`}
                title="Playback speed"
                heading="Speed"
                options={SPEEDS.map((s) => ({ value: s, label: s === 1 ? 'Normal' : `${s}x` }))}
                value={speed}
                onChange={changeSpeed}
              />
              {/* In fullscreen, switch between the original and the translated subtitles */}
              {fullscreen && trackOptions?.length > 1 && (
                <ControlMenu
                  className="track-btn"
                  label={
                    <>
                      <Languages size={16} />
                      <span>{trackOptions.find((t) => t.value === activeTrack)?.short}</span>
                    </>
                  }
                  title="Subtitle language"
                  heading="Subtitles"
                  options={trackOptions}
                  value={activeTrack}
                  onChange={onTrack}
                />
              )}
              <button
                className={`ctrl-btn cc-btn ${showCaptions ? 'active' : ''}`}
                onClick={() => setShowCaptions(!showCaptions)}
                title={showCaptions ? 'Hide subtitles (C)' : 'Show subtitles (C)'}
              >
                <Captions size={17} />
                <span className="cc-label">CC</span>
              </button>
              {fullscreen && (
                <button
                  className={`ctrl-btn transcript-btn ${showTranscript ? 'active' : ''}`}
                  onClick={() => setShowTranscript(!showTranscript)}
                  title={showTranscript ? 'Hide transcript (T)' : 'Show transcript (T)'}
                  aria-label={showTranscript ? 'Hide transcript' : 'Show transcript'}
                  aria-pressed={showTranscript}
                >
                  {showTranscript ? <PanelRightClose size={17} /> : <PanelRight size={17} />}
                </button>
              )}
              <button
                className="ctrl-btn fullscreen-btn"
                onClick={toggleFullscreen}
                title={fullscreen ? 'Exit fullscreen (F)' : 'Fullscreen (F)'}
                aria-label={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
              >
                {fullscreen ? <Minimize size={17} /> : <Maximize size={17} />}
              </button>
            </div>
          </div>
        </div>
      </div>

      {transcriptOpen && (
        <FsTranscript
          cues={cues}
          cueIndex={cueIndex}
          trackLabel={trackLabel}
          onSeek={seek}
          onEdit={onEdit}
          onStartEdit={() => videoRef.current?.pause()}
          onClose={() => setShowTranscript(false)}
        />
      )}
    </div>
  )
}
