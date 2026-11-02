// Loads YouTube's IFrame Player API once (https://developers.google.com/youtube/iframe_api_reference).
// Resolves to window.YT; rejects if the script can't load (offline, or blocked by the browser).

let loading = null

export function loadYouTubeApi() {
  if (window.YT?.Player) return Promise.resolve(window.YT)
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const previous = window.onYouTubeIframeAPIReady
      window.onYouTubeIframeAPIReady = () => {
        previous?.()
        resolve(window.YT)
      }
      const script = document.createElement('script')
      script.src = 'https://www.youtube.com/iframe_api'
      script.async = true
      script.onerror = () => {
        loading = null // let a later player try again
        reject(new Error('Could not load the YouTube player.'))
      }
      document.head.appendChild(script)
    })
  }
  return loading
}
