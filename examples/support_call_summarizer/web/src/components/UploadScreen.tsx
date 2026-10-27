import { useState, type FormEvent } from "react";
import type { AppConfig, Language } from "../api";
import { normaliseExtensions, validateFile } from "../lib/files";
import { formatLimitDuration } from "../lib/format";
import { DropZone } from "./DropZone";
import { AUTO_DETECT, LanguageSelect } from "./LanguageSelect";
import { Notice } from "./Notice";
import { TextField } from "./TextField";

export interface UploadDetails {
  /** null = auto-detect */
  language: string | null;
  ticketId: string;
  agentName: string;
}

export interface UploadDraft {
  file: File | null;
  details: UploadDetails;
}

interface UploadScreenProps {
  config: AppConfig;
  languages: Language[];
  /** Server-side error from the previous attempt. */
  submitError?: string | null;
  initial?: UploadDraft | null;
  onSubmit: (file: File, details: UploadDetails) => void;
}

export function UploadScreen({ config, languages, submitError, initial, onSubmit }: UploadScreenProps) {
  const [file, setFile] = useState<File | null>(initial?.file ?? null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [language, setLanguage] = useState(initial?.details.language ?? AUTO_DETECT);
  const [ticketId, setTicketId] = useState(initial?.details.ticketId ?? "");
  const [agentName, setAgentName] = useState(initial?.details.agentName ?? "");

  const extensions = normaliseExtensions(config.allowed_extensions);
  const hint = `${extensions.join(", ")} · up to ${config.max_file_mb} MB and ${formatLimitDuration(
    config.max_duration_seconds,
  )}`;

  const handleFile = (picked: File) => {
    const problem = validateFile(picked, config);
    setFileError(problem);
    setFile(problem ? null : picked);
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!file) {
      setFileError((current) => current ?? "Choose a call recording to process.");
      return;
    }
    const problem = validateFile(file, config);
    if (problem) {
      setFileError(problem);
      return;
    }
    onSubmit(file, { language: language || null, ticketId: ticketId.trim(), agentName: agentName.trim() });
  };

  return (
    <section className="card upload" aria-labelledby="upload-heading">
      <h2 id="upload-heading" className="card__title">
        Process a support call
      </h2>
      <p className="card__lead">
        Upload one recording. Long calls are split at pauses and processed in the background, so you can leave this
        page and come back.
      </p>

      {submitError && (
        <Notice tone="danger" role="alert" title="The call couldn't be uploaded">
          <p>{submitError}</p>
        </Notice>
      )}

      <form className="upload__form" onSubmit={handleSubmit} noValidate>
        <DropZone accept={extensions.join(",")} file={file} hint={hint} error={fileError} onFile={handleFile} />

        <LanguageSelect languages={languages} value={language} onChange={setLanguage} />

        <div className="upload__row">
          <TextField label="Ticket ID" optional value={ticketId} onChange={setTicketId} maxLength={200} />
          <TextField label="Agent name" optional value={agentName} onChange={setAgentName} maxLength={200} autoComplete="name" />
        </div>

        {config.diarization_enabled && (
          <p className="upload__info">
            Speakers will be labelled automatically (up to {config.max_speakers}), so you can tell the agent and
            customer apart.
          </p>
        )}

        <div className="upload__submit">
          <button type="submit" className="button button--primary button--large">
            Process call
          </button>
          <p className="field__hint">
            Recordings are deleted after processing. Results are kept for{" "}
            {formatLimitDuration(config.result_ttl_seconds)}.
          </p>
        </div>
      </form>
    </section>
  );
}
