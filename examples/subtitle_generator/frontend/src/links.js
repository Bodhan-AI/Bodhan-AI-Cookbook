// Video links, checked the same way as the backend (backend/app/download.py), so the page can explain early.

// Video file types the backend accepts (download.VIDEO_EXTENSIONS); also used for uploads
export const VIDEO_EXTENSIONS = ['.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v']

// Video sites: their links open a player page, not the video file, so they aren't supported
const SITE_HOSTS = [
  'youtube.com',
  'youtu.be',
  'vimeo.com',
  'instagram.com',
  'facebook.com',
  'fb.watch',
  'x.com',
  'twitter.com',
  'dailymotion.com',
  'tiktok.com',
  'twitch.tv',
  'linkedin.com',
  'reddit.com',
  'streamable.com',
]

function parse(link) {
  try {
    const url = new URL(link.trim())
    return url.protocol === 'http:' || url.protocol === 'https:' ? url : null
  } catch {
    return null
  }
}

export function isLink(link) {
  return parse(link) !== null
}

export function isSiteLink(link) {
  const host = parse(link)?.hostname.toLowerCase() ?? ''
  return SITE_HOSTS.some((h) => host === h || host.endsWith(`.${h}`))
}

// YouTube: the id of one video (watch?v=, youtu.be/, /shorts/, /live/, /embed/), or '' (not YouTube, or a playlist
// or channel link). Mirrors video_id() in backend/app/youtube.py.
const YOUTUBE_HOSTS = ['youtube.com', 'youtu.be', 'youtube-nocookie.com']
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/

export function isYouTubeLink(link) {
  const host = parse(link)?.hostname.toLowerCase() ?? ''
  return YOUTUBE_HOSTS.some((h) => host === h || host.endsWith(`.${h}`))
}

export function youtubeId(link) {
  if (!isYouTubeLink(link)) return ''
  const url = parse(link)
  const parts = url.pathname.split('/').filter(Boolean)
  let id = ''
  if (url.hostname.toLowerCase().endsWith('youtu.be')) id = parts[0] ?? ''
  else if (parts[0] === 'watch') id = url.searchParams.get('v') ?? ''
  else if (parts.length >= 2 && ['shorts', 'live', 'embed', 'v'].includes(parts[0])) id = parts[1]
  return VIDEO_ID.test(id) ? id : ''
}

// Any link that isn't a video site may be a video file, even without .mp4 in it (https://lorem.video/720p):
// worth trying in the player. The player says so if the browser can't play it.
export function isPreviewableLink(link) {
  return isLink(link) && !isSiteLink(link)
}
