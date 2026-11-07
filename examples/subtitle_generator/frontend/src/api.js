// The backend's job API. Every call resolves to parsed JSON or throws an Error with a readable message.

async function request(path, options, fallback) {
  let response
  try {
    response = await fetch(path, options)
  } catch {
    throw new Error('Could not reach the backend. Is it running on port 8000?')
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : fallback)
    error.status = response.status
    throw error
  }
  return data
}

// Upload a video and start a job. Resolves to the new job: { id, status, progress, step, ... }.
export function startJob(file, language, targetLanguage) {
  const body = new FormData()
  body.append('video', file)
  body.append('language', language)
  body.append('target_language', targetLanguage)
  return request('/api/jobs', { method: 'POST', body }, 'Could not start the job.')
}

// One job: progress while it runs; segments, original_srt, translated_segments, translated_srt once done.
export function getJob(id) {
  return request(`/api/jobs/${encodeURIComponent(id)}`, undefined, 'Could not load the job.')
}

// What the backend can do: { ffmpeg_found, api_key_set, max_upload_mb, direct_video_links, youtube_links, ... }.
export function getHealth() {
  return request('/api/health', undefined, 'Could not reach the backend.')
}

// Recent jobs, newest first, without their subtitles. One page of jobs: resolves to { jobs, total }.
export async function listJobs(offset = 0, limit = 10) {
  const data = await request(`/api/jobs?offset=${offset}&limit=${limit}`, undefined, 'Could not load the job history.')
  return { jobs: data.jobs ?? [], total: data.total ?? 0 }
}

// Finish a job that stopped during its translation. Resolves to the job, running again.
export function resumeJob(id) {
  return request(`/api/jobs/${encodeURIComponent(id)}/resume`, { method: 'POST' }, 'Could not resume the job.')
}

// Correct the text of one subtitle line ('original' or 'translated' track). Resolves to the updated job.
export function editLine(id, track, index, text) {
  return request(
    `/api/jobs/${encodeURIComponent(id)}/lines`,
    { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ track, index, text }) },
    'Could not save the change.'
  )
}

// Start a job from a link: a direct link to a video file, or a YouTube video (allowYouTube: the user's OK for
// YouTube links, asked each visit). Resolves to the new job.
export function startLinkJob(url, language, targetLanguage, allowYouTube = false) {
  return request(
    '/api/jobs/url',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, language, target_language: targetLanguage, allow_youtube: allowYouTube }),
    },
    'Could not start the job.'
  )
}
