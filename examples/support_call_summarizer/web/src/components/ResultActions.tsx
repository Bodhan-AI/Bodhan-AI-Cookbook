import { useState } from "react";
import { ApiError, type ApiClient, type CallRecord, type ExportFormat } from "../api";
import { copyText, saveBlob } from "../lib/browser";
import { transcriptText } from "../lib/transcript";

interface ResultActionsProps {
  record: CallRecord;
  api: Pick<ApiClient, "downloadExport">;
  onStartOver: () => void;
}

const EXPORTS: Array<{ format: ExportFormat; label: string }> = [
  { format: "json", label: "Export JSON" },
  { format: "calls_csv", label: "Export calls CSV" },
  { format: "segments_csv", label: "Export segments CSV" },
];

type Feedback = { tone: "success" | "danger"; text: string } | null;

export function ResultActions({ record, api, onStartOver }: ResultActionsProps) {
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const english = record.translation_available;

  const handleCopy = async () => {
    try {
      await copyText(transcriptText(record, english ? "english" : "original"));
      setFeedback({ tone: "success", text: english ? "English transcript copied." : "Transcript copied." });
    } catch {
      setFeedback({ tone: "danger", text: "Couldn't copy to the clipboard. Select the transcript text and copy it manually." });
    }
  };

  const handleExport = async (format: ExportFormat) => {
    setBusy(format);
    setFeedback(null);
    try {
      const { blob, filename } = await api.downloadExport(record.call_id, format);
      saveBlob(blob, filename);
      setFeedback({ tone: "success", text: `Downloaded ${filename}.` });
    } catch (err) {
      const text =
        err instanceof ApiError && err.status === 404
          ? "This result has expired on the server, so it can't be exported any more. Process the call again to get a fresh export."
          : err instanceof Error
            ? err.message
            : "The export failed. Please try again.";
      setFeedback({ tone: "danger", text });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="result-actions">
      <div className="result-actions__buttons" role="group" aria-label="Transcript actions">
        <button type="button" className="button button--primary" onClick={handleCopy}>
          {english ? "Copy English transcript" : "Copy transcript"}
        </button>
        {EXPORTS.map(({ format, label }) => (
          <button
            key={format}
            type="button"
            className="button button--secondary"
            onClick={() => handleExport(format)}
            disabled={busy !== null}
            aria-busy={busy === format || undefined}
          >
            {busy === format ? "Preparing…" : label}
          </button>
        ))}
        <button type="button" className="button button--ghost" onClick={onStartOver}>
          Process another call
        </button>
      </div>
      <p className={`result-actions__feedback${feedback ? ` result-actions__feedback--${feedback.tone}` : ""}`} aria-live="polite">
        {feedback?.text}
      </p>
    </div>
  );
}
