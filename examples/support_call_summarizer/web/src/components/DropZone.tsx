import { useEffect, useId, useRef, useState, type DragEvent } from "react";
import { formatBytes } from "../lib/format";

interface DropZoneProps {
  accept: string;
  file: File | null;
  hint: string;
  error: string | null;
  disabled?: boolean;
  onFile: (file: File) => void;
}

export function DropZone({ accept, file, hint, error, disabled, onFile }: DropZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();

  // A file dropped just outside the zone would otherwise navigate away from the app.
  useEffect(() => {
    const block = (e: globalThis.DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    };
    window.addEventListener("dragover", block);
    window.addEventListener("drop", block);
    return () => {
      window.removeEventListener("dragover", block);
      window.removeEventListener("drop", block);
    };
  }, []);

  const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer.types).includes("Files");

  const onDragEnter = (e: DragEvent<HTMLButtonElement>) => {
    if (disabled || !hasFiles(e)) return;
    e.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  };
  const onDragOver = (e: DragEvent<HTMLButtonElement>) => {
    if (disabled || !hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  };
  const onDragLeave = () => {
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  };
  const onDrop = (e: DragEvent<HTMLButtonElement>) => {
    e.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    if (disabled) return;
    const dropped = e.dataTransfer.files[0];
    if (dropped) onFile(dropped);
  };

  const classes = ["dropzone"];
  if (dragging) classes.push("dropzone--dragging");
  if (file) classes.push("dropzone--has-file");
  if (error) classes.push("dropzone--invalid");

  return (
    <div className="field">
      <label htmlFor={inputId} className="field__label">
        Call recording
      </label>
      <input
        ref={inputRef}
        id={inputId}
        className="visually-hidden"
        type="file"
        accept={accept}
        tabIndex={-1}
        disabled={disabled}
        onChange={(e) => {
          const picked = e.target.files?.[0];
          if (picked) onFile(picked);
          // Allow re-selecting the same file after fixing a problem.
          e.target.value = "";
        }}
      />
      <button
        type="button"
        className={classes.join(" ")}
        disabled={disabled}
        aria-describedby={error ? `${hintId} ${errorId}` : hintId}
        onClick={() => inputRef.current?.click()}
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <svg className="dropzone__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path
            d="M12 3a4 4 0 0 0-4 4v5a4 4 0 0 0 8 0V7a4 4 0 0 0-4-4Zm-7 9a1 1 0 1 1 2 0 5 5 0 0 0 10 0 1 1 0 1 1 2 0 7 7 0 0 1-6 6.93V21h2a1 1 0 1 1 0 2H9a1 1 0 1 1 0-2h2v-2.07A7 7 0 0 1 5 12Z"
            fill="currentColor"
          />
        </svg>
        {file ? (
          <>
            <span className="dropzone__title dropzone__filename">{file.name}</span>
            <span className="dropzone__subtitle">
              {formatBytes(file.size)} · Choose a different file
            </span>
          </>
        ) : (
          <>
            <span className="dropzone__title">Drop a call recording here</span>
            <span className="dropzone__subtitle">or press to browse your files</span>
          </>
        )}
      </button>
      <p id={hintId} className="field__hint">
        {hint}
      </p>
      {error && (
        <p id={errorId} className="field__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
