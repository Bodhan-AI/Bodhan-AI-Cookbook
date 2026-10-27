import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ApiClient, CallRecord, JobView, Language } from "../api";
import { useObjectUrl } from "../hooks/useObjectUrl";
import { formatTime } from "../lib/format";
import { languageName } from "../lib/languages";
import { activeSegmentNo, filterSegments } from "../lib/transcript";
import { AudioPlayer } from "./AudioPlayer";
import { CallDetails } from "./CallDetails";
import { CallSummary } from "./CallSummary";
import { Notice } from "./Notice";
import { ResultActions } from "./ResultActions";
import { Transcript } from "./Transcript";

interface ResultScreenProps {
  job: JobView;
  record: CallRecord;
  languages: Language[];
  /** The File uploaded in this session, if any; the server deletes audio after processing. */
  audioFile: File | null;
  api: Pick<ApiClient, "downloadExport">;
  onStartOver: () => void;
}

export function ResultScreen({ job, record, languages, audioFile, api, onStartOver }: ResultScreenProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const audioUrl = useObjectUrl(audioFile);
  const [audioFailed, setAudioFailed] = useState(false);
  const [hasPlayed, setHasPlayed] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [query, setQuery] = useState("");

  useEffect(() => headingRef.current?.focus(), []);

  const visibleSegments = useMemo(() => filterSegments(record.segments, query), [record.segments, query]);
  const playingSegment = hasPlayed ? activeSegmentNo(record.segments, currentTime) : null;
  const sourceLanguageName = languageName(record.source_language, languages);
  const canPlay = Boolean(audioUrl) && !audioFailed;

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = seconds;
    setCurrentTime(seconds);
    setHasPlayed(true);
    audio.play().catch(() => {
      // Autoplay policies may block play(); the user can press play themselves.
    });
  }, []);

  let audioNote: string | null = null;
  if (!audioFile) {
    audioNote = "Audio playback is only available in the browser session that uploaded the recording, because the server deletes audio after processing.";
  } else if (audioFailed) {
    audioNote = "This browser can't play this audio format, so playback is unavailable. The transcript is unaffected.";
  }

  return (
    <div className="result">
      <div className="result__header">
        <h2 ref={headingRef} tabIndex={-1} className="result__title">
          Call summary
        </h2>
        <p className="result__expiry">
          Results are available until {formatTime(job.expires_at)}. Export anything you need before then.
        </p>
      </div>

      {record.status === "partial" && (
        <Notice tone="warning" title="Some pieces of this call couldn't be processed.">
          <p>The transcript below is incomplete. Affected segments show what went wrong.</p>
          {record.warnings.length > 0 && (
            <ul className="notice__list">
              {record.warnings.map((warning, i) => (
                <li key={i}>{warning}</li>
              ))}
            </ul>
          )}
        </Notice>
      )}
      {record.status !== "partial" && record.warnings.length > 0 && (
        <Notice tone="info" title="Notes">
          <ul className="notice__list">
            {record.warnings.map((warning, i) => (
              <li key={i}>{warning}</li>
            ))}
          </ul>
        </Notice>
      )}

      <div className="result__top">
        <CallDetails record={record} languages={languages} />
        <section className="card result__actions-card" aria-label="Actions">
          <ResultActions record={record} api={api} onStartOver={onStartOver} />
        </section>
      </div>

      {record.analysis && <CallSummary analysis={record.analysis} />}

      {canPlay && audioUrl ? (
        <div className="result__player">
          <AudioPlayer
            ref={audioRef}
            src={audioUrl}
            fileName={record.source_file}
            onTimeUpdate={setCurrentTime}
            onPlay={() => setHasPlayed(true)}
            onError={() => setAudioFailed(true)}
          />
        </div>
      ) : (
        audioNote && <p className="result__audio-note">{audioNote}</p>
      )}

      <Transcript
        record={record}
        segments={visibleSegments}
        sourceLanguageName={sourceLanguageName}
        query={query}
        onQueryChange={setQuery}
        activeSegmentNo={playingSegment}
        onSeek={canPlay ? seek : undefined}
      />
    </div>
  );
}
