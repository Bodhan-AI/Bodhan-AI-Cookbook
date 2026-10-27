import { vi } from "vitest";
import type { ApiClient, AppConfig, CallAnalysis, CallRecord, JobView, Language, Segment } from "../api";

export const config: AppConfig = {
  max_file_mb: 100,
  max_duration_seconds: 3600,
  allowed_extensions: [".wav", ".mp3", ".m4a", ".ogg"],
  diarization_enabled: true,
  analysis_enabled: true,
  max_speakers: 8,
  result_ttl_seconds: 3600,
};

export const languages: Language[] = [
  { code: "hi", name: "Hindi", translation: true },
  { code: "ta", name: "Tamil", translation: true },
  { code: "bhb", name: "Bhili", translation: false },
  { code: "bho", name: "Bhojpuri", translation: false },
  { code: "en", name: "English", translation: true },
];

export const segments: Segment[] = [
  {
    segment_no: 1,
    start: 0,
    end: 5.2,
    speaker: "Speaker 1",
    original_text: "नमस्ते, मैं आपकी कैसे मदद कर सकता हूँ?",
    english_text: "Hello, how can I help you?",
    error: null,
    sentiment: "neutral",
  },
  {
    segment_no: 2,
    start: 5.2,
    end: 12.8,
    speaker: "Speaker 2",
    original_text: "मुझे अपने ऑर्डर का रिफंड चाहिए।",
    english_text: "I want a refund for my order.",
    error: null,
    sentiment: "negative",
  },
  {
    segment_no: 3,
    start: 12.8,
    end: 65.4,
    speaker: "Speaker 1",
    original_text: "ठीक है, मैं अभी जाँच करता हूँ।",
    english_text: "Okay, let me check right now.",
    error: null,
    sentiment: "neutral",
  },
];

export const analysis: CallAnalysis = {
  summary: "The customer asked for a refund for an order. The agent started checking the order.",
  customer_issue: "Wants a refund for an order.",
  resolution: "partially_resolved",
  action_items: ["Agent to confirm the refund by email"],
  topics: ["refund", "order"],
  overall_sentiment: "negative",
  sentiment_score: -0.4,
  customer_sentiment_trend: "steady",
  sentiment_explanation: "The customer was unhappy about the order.",
  model: "sarvam-105b",
};

export function makeRecord(overrides: Partial<CallRecord> = {}): CallRecord {
  return {
    call_id: "call-123",
    source_file: "customer-call.wav",
    ticket_id: "TCK-42",
    agent_name: "Priya",
    duration_seconds: 125,
    source_language: "hi",
    target_language: "en",
    translation_available: true,
    processed_at: "2026-10-20T10:15:00+05:30",
    status: "completed",
    speaker_count: 2,
    segments,
    full_text_original: segments.map((s) => s.original_text).join(" "),
    full_text_english: segments.map((s) => s.english_text).join(" "),
    analysis,
    warnings: [],
    ...overrides,
  };
}

export function makeJob(overrides: Partial<JobView> = {}): JobView {
  return {
    call_id: "call-123",
    status: "processing",
    progress: { stage: "transcribing", done: 12, total: 20, message: "Transcribing piece 12 of 20" },
    error: null,
    record: null,
    created_at: "2026-10-20T10:00:00+05:30",
    expires_at: "2026-10-20T11:15:00+05:30",
    ...overrides,
  };
}

export function makeCompletedJob(recordOverrides: Partial<CallRecord> = {}): JobView {
  const record = makeRecord(recordOverrides);
  return makeJob({
    status: record.status,
    progress: { stage: "done", done: 20, total: 20, message: "Done" },
    record,
  });
}

export function createMockApi(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    baseUrl: "",
    health: vi.fn(async () => ({ status: "ok" as const })),
    getConfig: vi.fn(async () => config),
    getLanguages: vi.fn(async () => languages),
    uploadCall: vi.fn(async () => ({ call_id: "call-123", status: "queued" as const })),
    getCall: vi.fn(async () => makeJob()),
    exportUrl: vi.fn((id: string, format: string) => `/api/calls/${id}/export?format=${format}`),
    downloadExport: vi.fn(async () => ({ blob: new Blob(["{}"]), filename: "call.json" })),
    ...overrides,
  };
}

export function fileOfSize(name: string, size: number, type = "audio/wav"): File {
  const file = new File(["x"], name, { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
}
