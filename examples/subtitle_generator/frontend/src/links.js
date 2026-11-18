// Video links, checked the same way as the backend (backend/app/download.py), so the page can explain early.

// Video file types the backend accepts (download.VIDEO_EXTENSIONS); also used for uploads
export const VIDEO_EXTENSIONS = ['.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v']

// YouTube gets its own message (upload the file; creators can download their own videos from YouTube Studio)
const YOUTUBE_HOSTS = ['youtube.com', 'youtu.be', 'youtube-nocookie.com']
// Video sites: their links open a player page, not the video file, so they aren't supported (download.SITE_HOSTS)
const SITE_HOSTS = [
  ...YOUTUBE_HOSTS,
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

function onHost(link, hosts) {
  const host = parse(link)?.hostname.toLowerCase() ?? ''
  return hosts.some((h) => host === h || host.endsWith(`.${h}`))
}

export function isSiteLink(link) {
  return onHost(link, SITE_HOSTS)
}

export function isYouTubeLink(link) {
  return onHost(link, YOUTUBE_HOSTS)
}

// Any link that isn't a video site may be a video file, even without .mp4 in it (https://lorem.video/720p):
// worth trying in the player. The player says so if the browser can't play it.
export function isPreviewableLink(link) {
  return isLink(link) && !isSiteLink(link)
}
