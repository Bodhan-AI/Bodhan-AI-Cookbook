import { useEffect, useRef, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, History as HistoryIcon, RefreshCw } from 'lucide-react'
import { languageName } from '../languages.js'

const STATUS_LABELS = { queued: 'Starting', running: 'Running', done: 'Ready', failed: 'Failed' }

function when(iso) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

// Past and running jobs, newest first, a page at a time. Opening a job shows its subtitles (or its error) again.
export default function History({ jobs, activeId, onOpen, onRefresh, page, pageSize, total, onPage }) {
  const [turning, setTurning] = useState(false)
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const first = page * pageSize + 1

  async function goTo(next) {
    setTurning(true)
    await onPage(next)
    setTurning(false)
  }

  const [refresh, setRefresh] = useState('idle') // idle, loading, done: shown on the button
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])

  async function reload() {
    clearTimeout(timer.current)
    setRefresh('loading')
    const started = Date.now()
    await onRefresh()
    // Keep the spinner up long enough to be seen, then confirm briefly
    await new Promise((resolve) => setTimeout(resolve, Math.max(0, 400 - (Date.now() - started))))
    setRefresh('done')
    timer.current = setTimeout(() => setRefresh('idle'), 1500)
  }

  return (
    <section className="panel history-panel" aria-labelledby="history-title">
      <div className="panel-heading">
        <HistoryIcon size={18} className="history-icon" />
        <div>
          <h2 id="history-title">History</h2>
          <p>Subtitles from earlier jobs. Videos are not kept, only the subtitle text.</p>
        </div>
        <button
          className={`copy-btn history-refresh ${refresh}`}
          onClick={reload}
          disabled={refresh === 'loading'}
          aria-busy={refresh === 'loading'}
          title="Reload the history"
        >
          {refresh === 'done' ? <Check size={14} /> : <RefreshCw size={14} className={refresh === 'loading' ? 'spin' : ''} />}
          <span aria-live="polite">{refresh === 'loading' ? 'Refreshing…' : refresh === 'done' ? 'Updated' : 'Refresh'}</span>
        </button>
      </div>

      {jobs.length === 0 ? (
        <p className="history-empty">No jobs yet. Your subtitle jobs will be listed here.</p>
      ) : (
        <ul className="history-list">
          {jobs.map((job) => (
            <li key={job.id}>
              <button
                className={`history-item ${job.id === activeId ? 'active' : ''}`}
                onClick={() => onOpen(job)}
                title={job.error || 'Show these subtitles'}
              >
                <span className="history-name">{job.file_name}</span>
                <span className="history-languages">
                  {languageName(job.language)}
                  {job.target_language && job.target_language !== job.language ? ` → ${languageName(job.target_language)}` : ''}
                </span>
                <span className="history-date">{when(job.created_at)}</span>
                <span className={`status-pill ${job.status}`}>
                  {STATUS_LABELS[job.status] ?? job.status}
                  {job.status === 'running' ? ` ${job.progress}%` : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {total > pageSize && (
        <nav className="history-pager" aria-label="History pages">
          <span className="history-range" aria-live="polite">
            {first}–{Math.min(first + jobs.length - 1, total)} of {total}
          </span>
          <div className="history-pager-buttons">
            <button className="pager-btn" onClick={() => goTo(page - 1)} disabled={page === 0 || turning} aria-label="Newer jobs">
              <ChevronLeft size={16} />
              <span>Newer</span>
            </button>
            <span className="history-page">
              Page {page + 1} of {pages}
            </span>
            <button className="pager-btn" onClick={() => goTo(page + 1)} disabled={page >= pages - 1 || turning} aria-label="Older jobs">
              <span>Older</span>
              <ChevronRight size={16} />
            </button>
          </div>
        </nav>
      )}
    </section>
  )
}
