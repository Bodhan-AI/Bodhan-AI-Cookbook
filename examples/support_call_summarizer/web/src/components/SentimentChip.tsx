import type { Sentiment } from "../api";

const LABELS: Record<Sentiment, string> = {
  positive: "Positive",
  neutral: "Neutral",
  negative: "Negative",
  mixed: "Mixed",
};

export function SentimentChip({ sentiment }: { sentiment: Sentiment }) {
  return <span className={`sentiment-chip sentiment-chip--${sentiment}`}>{LABELS[sentiment]}</span>;
}
