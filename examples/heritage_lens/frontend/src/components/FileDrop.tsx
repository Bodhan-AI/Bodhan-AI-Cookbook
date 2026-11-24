import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { Alert } from "./ui";

export default function FileDrop({ accept, extensions, maxMB, onFile, disabled, title, hint, progress }: {
  accept: string; extensions: string[]; maxMB: number; onFile: (f: File) => void; disabled?: boolean; title: string; hint?: string; progress?: number | null;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const take = (f?: File | null) => {
    if (!f) return;
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (!extensions.includes(ext)) return setError(`“${f.name}” is not an allowed file type. Allowed: ${extensions.join(", ")}.`);
    if (f.size > maxMB * 1024 * 1024) return setError(`“${f.name}” is larger than ${maxMB} MB.`);
    if (f.size === 0) return setError(`“${f.name}” is empty.`);
    setError(null); onFile(f);
  };
  return (
    <div>
      <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); if (!disabled) take(e.dataTransfer.files?.[0]); }}
        className={`flex flex-col items-center rounded-2xl border-2 border-dashed px-6 py-8 text-center transition ${over ? "border-clay bg-clay-pale" : "border-clay-muted bg-sand/50"}`}>
        <UploadCloud className="h-8 w-8 text-clay-soft" aria-hidden />
        <p className="mt-2 font-semibold">{title}</p>
        <p className="mt-1 text-xs text-mute">{hint ?? `${extensions.join(", ")} · up to ${maxMB} MB`}</p>
        <input ref={input} type="file" accept={accept} className="sr-only" id="file-input-hidden" disabled={disabled} onChange={(e) => { take(e.target.files?.[0]); e.target.value = ""; }} />
        <button type="button" className="btn-secondary mt-4" disabled={disabled} onClick={() => input.current?.click()}>Choose file</button>
        {progress != null && (
          <div className="mt-4 w-full max-w-sm" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)} aria-label="Upload progress">
            <div className="h-2 overflow-hidden rounded-full bg-line"><div className="h-full bg-clay transition-all" style={{ width: `${Math.round(progress * 100)}%` }} /></div>
            <p className="mt-1 text-xs">{progress < 1 ? `Uploading ${Math.round(progress * 100)}%` : "Upload finished, processing…"}</p>
          </div>)}
      </div>
      {error && <div className="mt-2"><Alert kind="error">{error}</Alert></div>}
    </div>
  );
}
