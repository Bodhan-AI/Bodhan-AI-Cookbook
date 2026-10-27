import type { CallRecord, Segment } from "../api";
import { Notice } from "./Notice";
import { SegmentRow } from "./SegmentRow";
import { TranscriptFilter } from "./TranscriptFilter";

interface TranscriptProps {
  record: CallRecord;
  segments: Segment[];
  sourceLanguageName: string;
  query: string;
  onQueryChange: (query: string) => void;
  activeSegmentNo: number | null;
  onSeek?: (seconds: number) => void;
}

export function Transcript({
  record,
  segments,
  sourceLanguageName,
  query,
  onQueryChange,
  activeSegmentNo,
  onSeek,
}: TranscriptProps) {
  const showEnglish = record.translation_available;
  const sourceLang = record.source_language ?? undefined;
  const originalHeading = record.source_language ? `Original (${sourceLanguageName})` : "Original";

  return (
    <section className="card transcript" aria-labelledby="transcript-heading">
      <div className="transcript__toolbar">
        <h3 id="transcript-heading" className="card__subtitle">
          Transcript
        </h3>
        <TranscriptFilter value={query} onChange={onQueryChange} matchCount={segments.length} total={record.segments.length} />
      </div>

      {!showEnglish && (
        <Notice tone="info">
          <p>
            English translation isn't available for {record.source_language ? sourceLanguageName : "this language"}.
            Showing the original-language transcript only.
          </p>
        </Notice>
      )}

      {record.segments.length === 0 ? (
        <p className="transcript__empty">No speech was found in this recording.</p>
      ) : segments.length === 0 ? (
        <p className="transcript__empty">No segments match “{query.trim()}”.</p>
      ) : (
        <div className={`transcript__body ${showEnglish ? "transcript--two-col" : "transcript--one-col"}`}>
          <div className="transcript__columns" aria-hidden="true">
            <span>{originalHeading}</span>
            {showEnglish && <span>English</span>}
          </div>
          <ol className="transcript__list">
            {segments.map((segment) => (
              <SegmentRow
                key={segment.segment_no}
                segment={segment}
                sourceLang={sourceLang}
                showEnglish={showEnglish}
                active={segment.segment_no === activeSegmentNo}
                query={query}
                onSeek={onSeek}
              />
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
