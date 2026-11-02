import { useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Captions, Clapperboard, Languages, Maximize, Minimize, PanelRight, PanelRightClose } from 'lucide-react'
import { loadYouTubeApi } from '../youtube.js'
import ControlMenu from './ControlMenu.jsx'
import FsTranscript from './FsTranscript.jsx'

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2] // all offered by YouTube's player
const TICK_MS = 250 // how often the playback time is read from the YouTube player
// Error codes from the IFrame API: 101 and 150 mean the owner doesn't allow the video to be embedded
const NOT_EMBEDDABLE = [101, 150]

// Plays a YouTube video (`videoId`) in YouTube's own embedded player, with the subtitle line being spoken shown in a
// strip under the video. YouTube's rules don't allow anything drawn over its player, so the line is never on top
// of it. Same props as VideoPlayer: `ref.current.seekTo(seconds)`, onActiveLine(index), and in fullscreen the
// transcript panel, where lines can be corrected with onEdit(index, text), and the trackOptions menu.
export default function YouTubePlayer({ ref, videoId, cues, trackLabel, trackOptions, activeTrack, onTrack, onActiveLine, onEdit }) {
  const containerRef = useRef(null)
  const frameRef = useRef(null)
  const playerRef = useRef(null)
  const [problem, setProblem] = useState('') // why the video can't play here
  const [time, setTime] = useState(0)
  const [showCaptions, setShowCaptions] = useState(true)
  const [fullscreen, setFullscreen] = useState(false)
  const [showTranscript, setShowTranscript] = useState(true)
  const [speed, setSpeed] = useState(1)

  // Create YouTube's player for this video (privacy-enhanced mode: youtube-nocookie.com), and read its time
  useEffect(() => {
    let player = null
    let timer
    let cancelled = false
    setProblem('')
    setTime(0)
    setSpeed(1)
    loadYouTubeApi().then(
      (YT) => {
        if (cancelled || !frameRef.current) return
        // YouTube replaces this element with its iframe, so it is created here and not by React
        const target = document.createElement('div')
        frameRef.current.replaceChildren(target)
        player = new YT.Player(target, {
          host: 'https://www.youtube-nocookie.com',
          videoId,
          width: '100%',
          height: '100%',
          // fs=0: use the fullscreen button below, which keeps the subtitle strip and the transcript in view
          playerVars: { playsinline: 1, rel: 0, fs: 0, iv_load_policy: 3, origin: window.location.origin },
          events: {
            // The speed can also be changed in YouTube's own settings menu
            onPlaybackRateChange: (e) => setSpeed(e.data),
            onReady: () => {
              playerRef.current = player
              timer = setInterval(() => setTime(player.getCurrentTime?.() ?? 0), TICK_MS)
            },
            onError: (e) =>
              setProblem(
                NOT_EMBEDDABLE.includes(e.data)
                  ? 'Its owner doesn’t allow it to be played on other sites. Subtitles still work.'
                  : 'YouTube can’t play this video here. Subtitles still work.'
              ),
          },
        })
      },
      () => !cancelled && setProblem('The YouTube player couldn’t load (offline, or blocked by the browser). Subtitles still work.')
    )
    return () => {
      cancelled = true
      clearInterval(timer)
      playerRef.current = null
      player?.destroy?.()
    }
  }, [videoId])

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === containerRef.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  // Shortcuts that belong to this page (inside the YouTube player, YouTube's own keys apply)
  useEffect(() => {
    function onKey(e) {
      if (document.activeElement?.closest?.('input, select, textarea')) return
      const actions = { KeyC: () => setShowCaptions((on) => !on), KeyF: toggleFullscreen, KeyT: () => setShowTranscript((on) => !on) }
      if (actions[e.code]) {
        e.preventDefault()
        actions[e.code]()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  function seek(seconds) {
    const player = playerRef.current
    if (!player) return
    player.seekTo(Math.max(0, seconds), true)
    setTime(seconds)
  }

  useImperativeHandle(ref, () => ({
    seekTo(seconds) {
      seek(seconds)
      playerRef.current?.playVideo()
    },
  }))

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
    else containerRef.current?.requestFullscreen().catch(() => {})
  }

  const cueIndex = cues.findIndex((c) => time >= c.start && time <= c.end)
  useEffect(() => onActiveLine?.(cueIndex), [cueIndex, onActiveLine])
  const cue = cues[cueIndex]
  const transcriptOpen = fullscreen && showTranscript

  if (problem) {
    return (
      <div className="video-container">
        <div className="video-empty-state">
          <span className="preview-glyph">
            <Clapperboard size={28} strokeWidth={1.5} />
          </span>
          <strong>This YouTube video can’t be played here</strong>
          <span>{problem}</span>
        </div>
      </div>
    )
  }

  return (
    <div ref={containerRef} className={`video-container youtube ${fullscreen ? 'is-fullscreen' : ''}`}>
      <div className="player-main">
        <div className="yt-frame" ref={frameRef} />

        {/* The line being spoken, under the video (never over YouTube's player) */}
        {showCaptions && (
          <div className="yt-caption-strip" aria-live="off">
            {cue ? (
              <span>{cue.text}</span>
            ) : (
              <span className="yt-caption-empty">{cues.length ? '' : 'Subtitles show here once they are ready'}</span>
            )}
          </div>
        )}

        <div className="player-controls-bar">
          <div className="controls-row">
            <div className="controls-left">
              <span className="yt-label">Playing on YouTube</span>
            </div>
            <div className="controls-right">
              <ControlMenu
                className="speed-btn"
                label={`${speed}x`}
                title="Playback speed"
                heading="Speed"
                options={SPEEDS.map((s) => ({ value: s, label: s === 1 ? 'Normal' : `${s}x` }))}
                value={speed}
                onChange={(s) => {
                  setSpeed(s)
                  playerRef.current?.setPlaybackRate(s)
                }}
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
                title={fullscreen ? 'Exit fullscreen (F)' : 'Fullscreen with subtitles (F)'}
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
          onStartEdit={() => playerRef.current?.pauseVideo()}
          onClose={() => setShowTranscript(false)}
        />
      )}
    </div>
  )
}
