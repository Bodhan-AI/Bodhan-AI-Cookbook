import { useEffect, useRef, useState } from "react";
import { Pause, Play, Volume2 } from "lucide-react";
import { useBlobUrl } from "../lib/hooks";
import { fmtDuration } from "../lib/format";
import { Button, Spinner } from "./ui";

/** Accessible custom audio player. `src` is an API path fetched with the auth header, or a ready blob/object URL. */
export default function AudioPlayer({ src, url, title, knownDuration, autoLoad = false }: { src?: string; url?: string; title: string; knownDuration?: number | null; autoLoad?: boolean }) {
  const [wanted, setWanted] = useState(autoLoad);
  const fetched = useBlobUrl(src, wanted && !!src);
  const objectUrl = url ?? fetched.url;
  const ref = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(knownDuration ?? 0);
  const [vol, setVol] = useState(1);
  const [rate, setRate] = useState(1);

  useEffect(() => { if (ref.current) { ref.current.volume = vol; ref.current.playbackRate = rate; } }, [vol, rate, objectUrl]);
  useEffect(() => { if (objectUrl && wanted && ref.current && !url) ref.current.play().catch(() => undefined); }, [objectUrl]); // eslint-disable-line

  if (!objectUrl) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-line bg-sand/50 p-3">
        <Button variant="secondary" onClick={() => setWanted(true)} loading={fetched.loading} aria-label={`Load and play ${title}`}><Play className="h-4 w-4" aria-hidden /> Play</Button>
        <span className="text-sm">{title}{knownDuration ? ` · ${fmtDuration(knownDuration)}` : ""}</span>
        {fetched.error && <span role="alert" className="text-sm font-semibold text-red-800">{fetched.error}</span>}
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-line bg-sand/50 p-3" role="group" aria-label={`Audio player: ${title}`}>
      <audio ref={ref} src={objectUrl} preload="metadata"
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setT(e.currentTarget.currentTime)} onLoadedMetadata={(e) => Number.isFinite(e.currentTarget.duration) && setDur(e.currentTarget.duration)} />
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn-primary !px-3" aria-label={playing ? "Pause" : "Play"} onClick={() => (playing ? ref.current?.pause() : ref.current?.play())}>
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </button>
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{title}</span>
        <span className="text-xs tabular-nums" aria-hidden>{fmtDuration(t)} / {fmtDuration(dur)}</span>
      </div>
      <input type="range" min={0} max={dur || 1} step={0.1} value={t} aria-label="Seek" className="mt-2 w-full accent-clay"
        aria-valuetext={`${fmtDuration(t)} of ${fmtDuration(dur)}`} onChange={(e) => { const v = Number(e.target.value); if (ref.current) ref.current.currentTime = v; setT(v); }} />
      <div className="mt-2 flex flex-wrap items-center gap-4 text-xs">
        <label className="flex items-center gap-2"><Volume2 className="h-3.5 w-3.5" aria-hidden /><span className="sr-only">Volume</span>
          <input type="range" min={0} max={1} step={0.05} value={vol} onChange={(e) => setVol(Number(e.target.value))} aria-label="Volume" className="w-24 accent-clay" /></label>
        <label className="flex items-center gap-2">Speed
          <select value={rate} onChange={(e) => setRate(Number(e.target.value))} className="rounded-md border border-line bg-white px-1.5 py-1">
            {[0.75, 1, 1.25, 1.5, 2].map((r) => <option key={r} value={r}>{r}×</option>)}</select></label>
      </div>
    </div>
  );
}
export { Spinner };
