import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Download, Info, RotateCcw } from 'lucide-react'
import Header from './components/Header.jsx'
import History from './components/History.jsx'
import JobProgress from './components/JobProgress.jsx'
import SubtitleList from './components/SubtitleList.jsx'
import UploadForm from './components/UploadForm.jsx'
import VideoPlayer from './components/VideoPlayer.jsx'
import YouTubePlayer from './components/YouTubePlayer.jsx'
import { languageName } from './languages.js'
import { editLine, getHealth, getJob, listJobs, resumeJob, startJob, startLinkJob } from './api.js'
import { isPreviewableLink, youtubeId } from './links.js'
import usePanelHeights from './usePanelHeights.js'

const POLL_MS = 1000
const FINISHED = ['done', 'failed']
const HISTORY_PAGE_SIZE = 10
const DEFAULT_SETTINGS = { language: 'hi', translate: false, target: 'en', source: 'file' }

// The start of the page intro: how a video can be given, by which kinds of link are switched on
function introSource(links) {
  if (links.direct && links.youtube) return 'Upload a video or paste a link (a direct video link or YouTube)'
  if (links.youtube) return 'Upload a video or paste a YouTube link'
  if (links.direct) return 'Upload a video or paste a direct video link'
  return 'Upload a video'
}

// The last source (file or link), language and translation choices, remembered in this browser
function savedSettings() {
  try {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem('settings') || '{}') }
  } catch {
    return DEFAULT_SETTINGS
  }
}

export default function App() {
  const [file, setFile] = useState(null)
  const [link, setLink] = useState('')
  const [settings, setSettings] = useState(savedSettings)
  // Which kinds of link the backend takes, switched in .env (DIRECT_VIDEO_LINKS, YOUTUBE_LINKS); none until
  // the backend says otherwise
  const [links, setLinks] = useState({ direct: false, youtube: false })
  const linksEnabled = links.direct || links.youtube
  const [allowYouTube, setAllowYouTube] = useState(false) // the user's OK for YouTube links: this visit only
  // The saved choice is kept while links are off, so it comes back when they are turned on
  const source = linksEnabled ? settings.source : 'file'
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [result, setResult] = useState(null)
  const [jobId, setJobId] = useState(null)
  const [pollRound, setPollRound] = useState(0) // bumped to follow the same job again after a resume
  const [job, setJob] = useState(null)
  const [history, setHistory] = useState([])
  const [historyPage, setHistoryPage] = useState(0)
  const [historyTotal, setHistoryTotal] = useState(0)
  const historyPageRef = useRef(0) // the page to reload, without making refreshHistory change
  const [activeTrack, setActiveTrack] = useState('original')
  const [activeIndex, setActiveIndex] = useState(-1) // the line being spoken, reported by the player
  const [maxUploadMb, setMaxUploadMb] = useState(100) // until /api/health says
  const [reconnecting, setReconnecting] = useState(false) // a status check failed; still retrying
  const playerRef = useRef(null)
  const workspaceRef = useRef(null)
  usePanelHeights(workspaceRef)

  useEffect(() => {
    getHealth().then(
      (health) => {
        setLinks({ direct: Boolean(health.direct_video_links), youtube: Boolean(health.youtube_links) })
        if (health.max_upload_mb) setMaxUploadMb(health.max_upload_mb)
      },
      () => {} // the history load below reports an unreachable backend
    )
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem('settings', JSON.stringify(settings))
    } catch {
      // Storage blocked: the choices still apply for this visit
    }
  }, [settings])

  // Load one page of the history (the current page by default). Resolves once loaded, or the error is shown.
  const refreshHistory = useCallback(
    (page = historyPageRef.current) =>
      listJobs(page * HISTORY_PAGE_SIZE, HISTORY_PAGE_SIZE).then(
        ({ jobs, total }) => {
          historyPageRef.current = page
          setHistoryPage(page)
          setHistory(jobs)
          setHistoryTotal(total)
        },
        (e) => setError(e.message)
      ),
    []
  )

  // On load: show the history, and pick up a job still running from before a page reload
  useEffect(() => {
    listJobs(0, HISTORY_PAGE_SIZE).then(
      ({ jobs, total }) => {
        setHistory(jobs)
        setHistoryTotal(total)
        const running = jobs.find((j) => !FINISHED.includes(j.status))
        if (running) {
          setJob(running)
          setJobId(running.id)
        }
      },
      (e) => setError(e.message)
    )
  }, [])

  // Follow the current job until it finishes
  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    let timer
    async function poll() {
      try {
        const data = await getJob(jobId)
        if (cancelled) return
        setReconnecting(false)
        setJob(data)
        if (!FINISHED.includes(data.status)) {
          timer = setTimeout(poll, POLL_MS)
          return
        }
        // A job that failed while translating still has its subtitles in the spoken language
        if (data.segments) {
          setResult({ ...data, target: data.target_language })
          setActiveTrack('original')
        }
        refreshHistory()
      } catch (e) {
        if (cancelled) return
        if (e.status === 404) {
          setError(e.message) // the job is gone: stop following it
          return
        }
        // Backend restarting or a network blip: keep trying; the job goes on running on the backend
        setReconnecting(true)
        timer = setTimeout(poll, POLL_MS * 3)
      }
    }
    poll()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [jobId, pollRound, refreshHistory])

  const busy = starting || (job !== null && !FINISHED.includes(job.status))

  function chooseFile(f) {
    setFile(f)
    setError('')
    setNotice('')
    // Choosing the video of the subtitles on show keeps them, so they can be previewed together
    if (!f || f.name !== result?.file_name) setResult(null)
  }

  function openJob(past) {
    setError('')
    setNotice('')
    setResult(null)
    if (file && file.name !== past.file_name) setFile(null)
    setJob(past)
    setJobId(past.id)
    setPollRound((n) => n + 1) // fetch it even when it is the job already open
  }

  function showError(message) {
    setError(message)
    setResult(null)
  }

  async function generate(chosen = settings, linkValue = link, allowed = allowYouTube) {
    setStarting(true)
    setError('')
    setNotice('')
    setResult(null)
    const { language, translate, target } = chosen
    const fromLink = linksEnabled && chosen.source === 'link'
    const targetLanguage = translate ? target : ''
    try {
      const started = fromLink
        ? await startLinkJob(linkValue.trim(), language, targetLanguage, allowed)
        : await startJob(file, language, targetLanguage)
      setJob(started)
      setJobId(started.id)
      refreshHistory(0) // the new job is first
    } catch (e) {
      setError(e.message)
    } finally {
      setStarting(false)
    }
  }

  async function resume() {
    setStarting(true)
    setError('')
    try {
      const resumed = await resumeJob(job.id)
      setJob(resumed)
      setPollRound((n) => n + 1)
      refreshHistory()
    } catch (e) {
      setError(e.message)
    } finally {
      setStarting(false)
    }
  }

  // Run a failed job from the start with its settings. An uploaded video isn't kept, so it has to be chosen again;
  // a link is, so a link job starts again at once
  function runAgain() {
    const translate = Boolean(job.target_language) && job.target_language !== job.language
    const fromLink = ['url', 'youtube'].includes(job.source) && job.source_url
    const fromYouTube = fromLink && job.source === 'youtube'
    if (fromYouTube && !links.youtube) {
      setNotice('YouTube links are turned off (YOUTUBE_LINKS in .env). Upload the video file instead.')
      return
    }
    if (fromLink && !fromYouTube && !links.direct) {
      setNotice('Links to video files are turned off (DIRECT_VIDEO_LINKS in .env). Upload the video file instead.')
      return
    }
    const chosen = {
      ...settings,
      language: job.language,
      translate,
      target: translate ? job.target_language : settings.target,
      source: fromLink ? 'link' : 'file',
    }
    setSettings(chosen)
    if (fromYouTube && !allowYouTube) {
      setLink(job.source_url) // the link is filled in; the user turns on "Allow YouTube links", then Generate
      setNotice('Turn on “Allow YouTube links” on the left, then press Generate.')
    } else if (fromLink) {
      setLink(job.source_url)
      generate(chosen, job.source_url)
    } else if (file?.name === job.file_name) {
      generate(chosen)
    } else {
      setNotice(`Choose ${job.file_name} on the left, then press Generate. Its settings are filled in.`)
    }
  }

  // Save a corrected line; the downloads are rebuilt from the saved lines
  async function saveLine(trackId, index, text) {
    const data = await editLine(result.id, trackId, index, text)
    setResult({ ...data, target: data.target_language })
  }

  const failed = job?.status === 'failed' ? job : null

  // Links to a video file can be played here (the player says so if one can't); YouTube videos play in YouTube's
  // player; other video-site links can't be previewed
  const resultLink = !file && result?.source === 'url' && isPreviewableLink(result.source_url ?? '') ? result.source_url : ''
  const typedLink = !file && source === 'link' && links.direct && isPreviewableLink(link) ? link.trim() : ''
  const videoUrl = resultLink || typedLink
  const resultYouTube = !file && result?.source === 'youtube' ? youtubeId(result.source_url ?? '') : ''
  const typedYouTube = !file && source === 'link' && links.youtube ? youtubeId(link) : ''
  const youtubeVideo = resultYouTube || (!resultLink && typedYouTube)

  // The followed job's live progress replaces its row from the last history load
  const historyRows = history.map((h) => (h.id === job?.id ? job : h))

  // The original subtitles, plus the translation if one was made
  const tracks = result
    ? [
        {
          id: 'original',
          label: 'Original',
          language: languageName(result.language),
          code: result.language,
          segments: result.segments,
          srt: result.original_srt,
        },
        ...(result.translated_srt
          ? [
              {
                id: 'translated',
                label: 'Translated',
                language: languageName(result.target),
                code: result.target,
                segments: result.translated_segments,
                srt: result.translated_srt,
              },
            ]
          : []),
      ]
    : []
  const shownTrack = tracks.find((t) => t.id === activeTrack)
  const cues = shownTrack?.segments ?? []
  // For the players' subtitle-language menu (shown in fullscreen when there is a translation)
  const trackOptions = tracks.map((t) => ({ value: t.id, label: `${t.label} · ${t.language}`, short: t.language }))

  return (
    <div className="app-container">
      <Header />

      <section className="hero">
        <h1>
          Subtitles for your video, <span className="accent">in its language and yours</span>.
        </h1>
        <p>
          {introSource(links)}, pick the spoken language, and get SRT files you can preview, correct and download. Transcription by Bodhan
          IndicTranscribe, translation by IndicTranslate.
        </p>
      </section>

      <main className="workspace" ref={workspaceRef}>
        <UploadForm
          file={file}
          onFile={chooseFile}
          onError={showError}
          link={link}
          onLink={(value) => {
            setLink(value)
            setError('')
            setNotice('')
          }}
          links={links}
          allowYouTube={allowYouTube}
          onAllowYouTube={(on) => {
            setAllowYouTube(on)
            setNotice('')
          }}
          maxMb={maxUploadMb}
          settings={settings}
          onSettings={setSettings}
          busy={busy}
          onGenerate={() => generate()}
        />

        <section className="panel preview-panel" aria-labelledby="step-2-title">
          <div className="panel-heading">
            <span className="step-badge">2</span>
            <div>
              <h2 id="step-2-title">Preview and download</h2>
              <p>Check the subtitles against the video, then save them.</p>
            </div>
          </div>

          {youtubeVideo ? (
            <YouTubePlayer
              ref={playerRef}
              videoId={youtubeVideo}
              cues={cues}
              trackLabel={shownTrack && `${shownTrack.label} · ${shownTrack.language}`}
              trackOptions={trackOptions}
              activeTrack={shownTrack?.id}
              onTrack={setActiveTrack}
              onActiveLine={setActiveIndex}
              onEdit={shownTrack && ((index, text) => saveLine(shownTrack.id, index, text))}
            />
          ) : (
            <VideoPlayer
              ref={playerRef}
              file={file}
              videoUrl={videoUrl}
              cues={cues}
              trackLabel={shownTrack && `${shownTrack.label} · ${shownTrack.language}`}
              trackOptions={trackOptions}
              activeTrack={shownTrack?.id}
              onTrack={setActiveTrack}
              onActiveLine={setActiveIndex}
              onEdit={shownTrack && ((index, text) => saveLine(shownTrack.id, index, text))}
            />
          )}

          {error && (
            <div className="notice-banner error" role="alert">
              <AlertCircle size={18} />
              <span>{error}</span>
            </div>
          )}

          {failed && (
            <div className="notice-banner error" role="alert">
              <AlertCircle size={18} />
              <div className="notice-body">
                <span>{failed.error || 'The job failed.'}</span>
                <button className="notice-action" onClick={failed.resumable ? resume : runAgain} disabled={busy}>
                  <RotateCcw size={14} />
                  <span>{failed.resumable ? 'Resume translation' : 'Run again'}</span>
                </button>
              </div>
            </div>
          )}

          {reconnecting && (
            <div className="notice-banner info" role="status">
              <Info size={18} />
              <span>Lost contact with the backend. Trying again… The job keeps running on the backend.</span>
            </div>
          )}

          {notice && (
            <div className="notice-banner info" role="status">
              <Info size={18} />
              <span>{notice}</span>
            </div>
          )}

          {busy && job && <JobProgress job={job} />}

          {result ? (
            <>
              {!failed && (
                <div className="notice-banner success" role="status">
                  <CheckCircle2 size={18} />
                  <span>
                    {file || videoUrl || youtubeVideo
                      ? 'Subtitles ready. Click any line to jump to that point in the video.'
                      : result.source === 'url'
                        ? `Subtitles for ${result.file_name}. This link can't be played here; the SRT files are ready to download.`
                        : `Subtitles for ${result.file_name}. Choose that video on the left to preview them with it.`}
                  </span>
                </div>
              )}
              <SubtitleList
                tracks={tracks}
                activeTrack={activeTrack}
                onTrack={setActiveTrack}
                activeIndex={activeIndex}
                onSeek={(seconds) => playerRef.current?.seekTo(seconds)}
                onEdit={saveLine}
              />
            </>
          ) : (
            !busy && (
              <div className="empty-placeholder">
                <Download size={18} />
                <span>Subtitle lines and downloads will appear here.</span>
              </div>
            )
          )}
        </section>
      </main>

      <History
        jobs={historyRows}
        activeId={result?.id}
        onOpen={openJob}
        onRefresh={() => refreshHistory()}
        page={historyPage}
        pageSize={HISTORY_PAGE_SIZE}
        total={historyTotal}
        onPage={refreshHistory}
      />

      <footer className="footer">
        <span>Subtitle Generator · Bodhan AI Cookbook</span>
        <span className="footer-links">indic-transcribe · indic-translate · FFmpeg</span>
      </footer>
    </div>
  )
}
