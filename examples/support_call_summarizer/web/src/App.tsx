import { useCallback, useEffect, useRef, useState } from "react";
import { isAbortError, type ApiClient } from "./api";
import { AppFooter } from "./components/AppFooter";
import { AppHeader } from "./components/AppHeader";
import { Notice } from "./components/Notice";
import { PageIntro } from "./components/PageIntro";
import { ProgressScreen } from "./components/ProgressScreen";
import { ResultScreen } from "./components/ResultScreen";
import { UploadScreen, type UploadDetails, type UploadDraft } from "./components/UploadScreen";
import { useAppMeta } from "./hooks/useAppMeta";
import { useCallJob } from "./hooks/useCallJob";
import { clearActiveCallId, hasCallIdInUrl, readActiveCallId, saveActiveCallId } from "./lib/activeCall";
import { api as defaultApi } from "./lib/apiInstance";

interface AppProps {
  api?: ApiClient;
}

interface UploadState {
  fileName: string;
  fraction: number;
}

export function App({ api = defaultApi }: AppProps) {
  const meta = useAppMeta(api);
  const [callId, setCallId] = useState<string | null>(() => readActiveCallId());
  // A call remembered only in localStorage (not an explicit ?call= link) that the
  // server has since dropped should not block the start page with an error.
  const resumedFromStorage = useRef(callId !== null && !hasCallIdInUrl());
  const [upload, setUpload] = useState<UploadState | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [draft, setDraft] = useState<UploadDraft | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const uploadAbort = useRef<AbortController | null>(null);
  const job = useCallJob(api, callId);

  useEffect(() => {
    if (callId) saveActiveCallId(callId);
  }, [callId]);

  const handleSubmit = useCallback(
    async (file: File, details: UploadDetails) => {
      const controller = new AbortController();
      uploadAbort.current = controller;
      resumedFromStorage.current = false;
      setDraft({ file, details });
      setSubmitError(null);
      setUpload({ fileName: file.name, fraction: 0 });
      try {
        const created = await api.uploadCall(
          { file, language: details.language, ticketId: details.ticketId, agentName: details.agentName },
          {
            signal: controller.signal,
            onProgress: (fraction) => setUpload((u) => (u ? { ...u, fraction } : u)),
          },
        );
        setAudioFile(file);
        setDraft(null);
        setCallId(created.call_id);
      } catch (err) {
        if (!isAbortError(err)) {
          setSubmitError(err instanceof Error ? err.message : "The upload failed. Please try again.");
        }
      } finally {
        if (uploadAbort.current === controller) uploadAbort.current = null;
        setUpload(null);
      }
    },
    [api],
  );

  const cancelUpload = useCallback(() => uploadAbort.current?.abort(), []);

  const startOver = useCallback(() => {
    uploadAbort.current?.abort();
    clearActiveCallId();
    setCallId(null);
    setAudioFile(null);
    setDraft(null);
    setSubmitError(null);
  }, []);

  useEffect(() => {
    if (job.notFound && resumedFromStorage.current) {
      resumedFromStorage.current = false;
      startOver();
    }
  }, [job.notFound, startOver]);

  const diarizationEnabled = meta.config?.diarization_enabled ?? false;
  const analysisEnabled = meta.config?.analysis_enabled ?? false;

  let content;
  let wideLayout = false;
  if (upload) {
    content = (
      <ProgressScreen
        fileName={upload.fileName}
        diarizationEnabled={diarizationEnabled}
        analysisEnabled={analysisEnabled}
        uploadFraction={upload.fraction}
        job={null}
        onCancelUpload={cancelUpload}
        onStartOver={startOver}
      />
    );
  } else if (callId) {
    const view = job.job;
    const finished = view && (view.status === "completed" || view.status === "partial") && view.record;
    wideLayout = Boolean(finished);
    content =
      finished && view.record ? (
        <ResultScreen
          key={view.call_id}
          job={view}
          record={view.record}
          languages={meta.languages}
          audioFile={audioFile}
          api={api}
          onStartOver={startOver}
        />
      ) : (
        <ProgressScreen
          fileName={audioFile?.name ?? null}
          diarizationEnabled={diarizationEnabled}
        analysisEnabled={analysisEnabled}
          uploadFraction={null}
          job={view}
          connectionLost={job.connectionLost}
          notFound={job.notFound}
          error={job.error}
          onRetryNow={job.retryNow}
          onStartOver={startOver}
        />
      );
  } else if (meta.status === "ready") {
    content = (
      <UploadScreen
        config={meta.config}
        languages={meta.languages}
        submitError={submitError}
        initial={draft}
        onSubmit={handleSubmit}
      />
    );
  } else if (meta.status === "error") {
    content = (
      <Notice
        tone="danger"
        role="alert"
        title="Couldn't load settings from the server."
        actions={
          <button type="button" className="button button--primary" onClick={meta.reload}>
            Try again
          </button>
        }
      >
        <p>{meta.error}</p>
      </Notice>
    );
  } else {
    content = (
      <p className="app-loading" role="status">
        Loading…
      </p>
    );
  }

  return (
    <div className="app">
      <AppHeader />
      <main className="app-main">
        <PageIntro wide={wideLayout} />
        {content}
      </main>
      <AppFooter />
    </div>
  );
}
