import { useEffect, useRef } from "react";
import type { JobView } from "../api";
import { percent } from "../lib/format";
import { buildSteps } from "../lib/steps";
import { Notice } from "./Notice";
import { ProgressBar } from "./ProgressBar";
import { Stepper } from "./Stepper";

interface ProgressScreenProps {
  fileName: string | null;
  diarizationEnabled: boolean;
  analysisEnabled?: boolean;
  /** Non-null while the file is being uploaded. */
  uploadFraction: number | null;
  job: JobView | null;
  connectionLost?: boolean;
  notFound?: boolean;
  error?: string | null;
  onRetryNow?: () => void;
  onCancelUpload?: () => void;
  onStartOver: () => void;
}

export function ProgressScreen({
  fileName,
  diarizationEnabled,
  analysisEnabled = false,
  uploadFraction,
  job,
  connectionLost = false,
  notFound = false,
  error = null,
  onRetryNow,
  onCancelUpload,
  onStartOver,
}: ProgressScreenProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => headingRef.current?.focus(), []);

  const uploading = uploadFraction !== null;
  const failed = job?.status === "failed";
  const displayName = fileName ?? job?.record?.source_file ?? null;

  const startOverButton = (
    <button type="button" className="button button--primary" onClick={onStartOver}>
      Start over
    </button>
  );

  if (notFound) {
    return (
      <section className="card progress-screen" aria-labelledby="progress-heading">
        <h2 id="progress-heading" ref={headingRef} tabIndex={-1} className="card__title">
          Result not available
        </h2>
        <Notice tone="warning" role="alert" title="This result has expired or was not found." actions={startOverButton}>
          <p>Results are kept on the server for a limited time after processing. Upload the recording again to process it.</p>
        </Notice>
      </section>
    );
  }

  const steps = buildSteps({
    diarizationEnabled,
    analysisEnabled,
    stage: uploading ? null : (job?.progress.stage ?? "queued"),
    status: uploading ? null : (job?.status ?? null),
  });

  let message: string;
  let value: number | null = null;
  if (uploading) {
    message = uploadFraction >= 1 ? "Upload complete. Starting processing…" : `Uploading ${displayName ?? "recording"}…`;
    value = uploadFraction;
  } else if (!job) {
    message = "Checking progress…";
  } else {
    message = job.progress.message || "Working on your call…";
    if (job.progress.total > 0) value = job.progress.done / job.progress.total;
  }

  return (
    <section className="card progress-screen" aria-labelledby="progress-heading">
      <h2 id="progress-heading" ref={headingRef} tabIndex={-1} className="card__title">
        {failed ? "Processing failed" : "Processing your call"}
      </h2>
      {displayName && <p className="progress-screen__file">{displayName}</p>}

      <Stepper steps={steps} />

      {failed || error ? (
        <Notice
          tone="danger"
          role="alert"
          title={failed ? "We couldn't process this call." : "Something went wrong."}
          actions={startOverButton}
        >
          <p>{(failed ? job?.error?.message : error) ?? "An unexpected error occurred."}</p>
        </Notice>
      ) : (
        <div className="progress-screen__status">
          <p className="progress-screen__message" aria-live="polite">
            {message}
          </p>
          <div className="progress-screen__bar">
            <ProgressBar value={value} label={uploading ? "Upload progress" : "Processing progress"} />
            {value !== null && <span className="progress-screen__percent">{percent(value)}%</span>}
          </div>
        </div>
      )}

      {connectionLost && !failed && (
        <Notice
          tone="warning"
          role="status"
          title="Connection lost — retrying…"
          actions={
            onRetryNow && (
              <button type="button" className="button button--secondary" onClick={onRetryNow}>
                Retry now
              </button>
            )
          }
        >
          <p>Your call is still being processed on the server. Progress will update when the connection is back.</p>
        </Notice>
      )}

      {!failed && !error && (
        <div className="progress-screen__footer">
          {uploading ? (
            <button type="button" className="button button--secondary" onClick={onCancelUpload} disabled={!onCancelUpload}>
              Cancel upload
            </button>
          ) : (
            <>
              <p className="field__hint">
                You can leave this page. Come back to this tab or link and progress will pick up where it left off.
              </p>
              <button type="button" className="button button--ghost" onClick={onStartOver}>
                Process a different call
              </button>
            </>
          )}
        </div>
      )}
    </section>
  );
}
