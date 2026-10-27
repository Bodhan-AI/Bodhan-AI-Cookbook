import { percent } from "../lib/format";

interface ProgressBarProps {
  /** 0..1, or null for indeterminate. */
  value: number | null;
  label: string;
}

export function ProgressBar({ value, label }: ProgressBarProps) {
  const pct = value === null ? null : percent(value);
  return (
    <div
      className={`progress${pct === null ? " progress--indeterminate" : ""}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={pct === null ? undefined : 0}
      aria-valuemax={pct === null ? undefined : 100}
      aria-valuenow={pct ?? undefined}
    >
      <div className="progress__fill" style={pct === null ? undefined : { width: `${pct}%` }} />
    </div>
  );
}
