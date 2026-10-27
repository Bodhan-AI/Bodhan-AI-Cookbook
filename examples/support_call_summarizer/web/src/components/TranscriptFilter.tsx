import { useId } from "react";

interface TranscriptFilterProps {
  value: string;
  onChange: (value: string) => void;
  matchCount: number;
  total: number;
}

export function TranscriptFilter({ value, onChange, matchCount, total }: TranscriptFilterProps) {
  const id = useId();
  const countId = useId();
  const filtering = value.trim() !== "";
  const noun = total === 1 ? "segment" : "segments";

  return (
    <div className="transcript-filter">
      <label htmlFor={id} className="field__label">
        Filter transcript
      </label>
      <input
        id={id}
        className="input"
        type="search"
        value={value}
        placeholder="Search original or English text"
        aria-describedby={countId}
        onChange={(e) => onChange(e.target.value)}
      />
      <p id={countId} className="transcript-filter__count" aria-live="polite">
        {filtering ? `${matchCount} of ${total} ${noun} match` : `${total} ${noun}`}
      </p>
    </div>
  );
}
