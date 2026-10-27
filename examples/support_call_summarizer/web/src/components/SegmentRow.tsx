import { memo } from "react";
import type { Segment } from "../api";
import { formatTimeRange, formatTimestamp } from "../lib/format";
import { speakerColorIndex } from "../lib/speakers";
import { Highlight } from "./Highlight";
import { SentimentChip } from "./SentimentChip";
import { SpeakerChip } from "./SpeakerChip";

interface SegmentRowProps {
  segment: Segment;
  sourceLang: string | undefined;
  showEnglish: boolean;
  active: boolean;
  query: string;
  onSeek?: (seconds: number) => void;
}

export const SegmentRow = memo(function SegmentRow({ segment, sourceLang, showEnglish, active, query, onSeek }: SegmentRowProps) {
  const range = formatTimeRange(segment.start, segment.end);
  const classes = ["segment"];
  if (segment.speaker) classes.push(`segment--speaker-${speakerColorIndex(segment.speaker)}`);
  if (active) classes.push("segment--active");
  if (segment.error) classes.push("segment--error");

  return (
    <li className={classes.join(" ")} aria-current={active ? "true" : undefined} data-segment={segment.segment_no}>
      <div className="segment__meta">
        {onSeek ? (
          <button
            type="button"
            className="segment__time segment__time--button"
            title={`Play from ${formatTimestamp(segment.start)}`}
            onClick={() => onSeek(segment.start)}
          >
            {range}
          </button>
        ) : (
          <span className="segment__time">{range}</span>
        )}
        {segment.speaker && <SpeakerChip speaker={segment.speaker} />}
        {/* neutral is the common case, so only the notable tones are tagged */}
        {(segment.sentiment === "positive" || segment.sentiment === "negative") && (
          <SentimentChip sentiment={segment.sentiment} />
        )}
      </div>

      {segment.error && <p className="segment__error">This piece couldn't be fully processed: {segment.error}</p>}

      <div className="segment__texts">
        <div className="segment__text segment__text--original">
          <span className="segment__label">Original</span>
          {segment.original_text ? (
            <p lang={sourceLang}>
              <Highlight text={segment.original_text} query={query} />
            </p>
          ) : (
            <p className="segment__missing">No transcript for this piece.</p>
          )}
        </div>
        {showEnglish && (
          <div className="segment__text segment__text--english">
            <span className="segment__label">English</span>
            {segment.english_text ? (
              <p lang="en">
                <Highlight text={segment.english_text} query={query} />
              </p>
            ) : (
              <p className="segment__missing">Translation not available</p>
            )}
          </div>
        )}
      </div>
    </li>
  );
});
