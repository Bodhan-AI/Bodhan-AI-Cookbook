// TypeScript mirror of the backend's API models. Platform-neutral so the
// future React Native app can import it unchanged.

export type CallStatus = "queued" | "processing" | "completed" | "partial" | "failed";

export type Stage =
  | "queued"
  | "converting"
  | "diarizing"
  | "segmenting"
  | "transcribing"
  | "translating"
  | "analyzing"
  | "done";

/** "mixed" is only used for the whole call, never for one segment. */
export type Sentiment = "positive" | "neutral" | "negative" | "mixed";
export type Resolution = "resolved" | "partially_resolved" | "unresolved" | "unclear";
export type SentimentTrend = "improved" | "steady" | "worsened";

export interface AppConfig {
  max_file_mb: number;
  max_duration_seconds: number;
  /** Lower-case, with the leading dot, e.g. ".wav". */
  allowed_extensions: string[];
  diarization_enabled: boolean;
  /** True when the server has a Sarvam key and adds an AI summary and sentiment. */
  analysis_enabled: boolean;
  max_speakers: number;
  result_ttl_seconds: number;
}

export interface Language {
  code: string;
  name: string;
  /** False when English translation is not offered for this language. */
  translation: boolean;
}

export interface LanguagesResponse {
  languages: Language[];
}

export interface CreateCallResponse {
  call_id: string;
  status: "queued";
}

export interface Progress {
  stage: Stage;
  done: number;
  total: number;
  /** Human readable, e.g. "Transcribing piece 12 of 20". */
  message: string;
}

export interface JobError {
  code: string;
  message: string;
}

export interface Segment {
  segment_no: number;
  start: number;
  end: number;
  /** "Speaker 1".."Speaker 8", or null when diarization is off/unavailable. */
  speaker: string | null;
  original_text: string;
  english_text: string | null;
  error: string | null;
  /** Null when the call was not analysed or this piece has no text. */
  sentiment: Sentiment | null;
}

/** AI summary and sentiment of the whole call, from the Sarvam LLM. */
export interface CallAnalysis {
  summary: string;
  customer_issue: string;
  resolution: Resolution;
  action_items: string[];
  topics: string[];
  overall_sentiment: Sentiment;
  /** -1 (very negative) to 1 (very positive). */
  sentiment_score: number;
  customer_sentiment_trend: SentimentTrend;
  sentiment_explanation: string;
  model: string;
}

export interface CallRecord {
  call_id: string;
  source_file: string;
  ticket_id: string | null;
  agent_name: string | null;
  duration_seconds: number;
  source_language: string | null;
  target_language: string;
  translation_available: boolean;
  /** ISO 8601 with offset. */
  processed_at: string;
  status: CallStatus;
  speaker_count: number | null;
  segments: Segment[];
  full_text_original: string;
  full_text_english: string;
  /** Null when analysis is off on the server or failed (see warnings). */
  analysis: CallAnalysis | null;
  warnings: string[];
}

export interface JobView {
  call_id: string;
  status: CallStatus;
  progress: Progress;
  error: JobError | null;
  /** Non-null for "completed" and "partial"; may be present for "failed". */
  record: CallRecord | null;
  created_at: string;
  expires_at: string;
}

export interface HealthResponse {
  status: "ok";
}

export interface ErrorBody {
  error: JobError;
}

export type ExportFormat = "json" | "calls_csv" | "segments_csv";

export const TERMINAL_STATUSES: readonly CallStatus[] = ["completed", "partial", "failed"];

export function isTerminalStatus(status: CallStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}
