import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type CallRecord } from "../api";
import { ResultScreen } from "../components/ResultScreen";
import { createMockApi, languages, makeCompletedJob, segments } from "./fixtures";

function renderResult(recordOverrides: Partial<CallRecord> = {}, extra: { audioFile?: File | null; api?: ReturnType<typeof createMockApi> } = {}) {
  const job = makeCompletedJob(recordOverrides);
  const api = extra.api ?? createMockApi();
  const onStartOver = vi.fn();
  const utils = render(
    <ResultScreen
      job={job}
      record={job.record!}
      languages={languages}
      audioFile={extra.audioFile ?? null}
      api={api}
      onStartOver={onStartOver}
    />,
  );
  return { ...utils, api, onStartOver };
}

function transcriptRows(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLLIElement>(".transcript__list > li"));
}

describe("Result screen", () => {
  it("renders call details with formatted duration, language and status", () => {
    renderResult();
    const details = screen.getByRole("region", { name: "Call details" });
    expect(within(details).getByText("customer-call.wav")).toBeInTheDocument();
    expect(within(details).getByText("02:05")).toBeInTheDocument();
    expect(within(details).getByText("Hindi")).toBeInTheDocument();
    expect(within(details).getByText("TCK-42")).toBeInTheDocument();
    expect(within(details).getByText("Priya")).toBeInTheDocument();
    expect(within(details).getByText("2")).toBeInTheDocument();
    expect(within(details).getByText("Completed")).toBeInTheDocument();
  });

  it("shows Auto-detected when the source language is unknown", () => {
    renderResult({ source_language: null });
    expect(within(screen.getByRole("region", { name: "Call details" })).getByText("Auto-detected")).toBeInTheDocument();
  });

  it("renders one row per segment with timestamps, speakers, original and English text side by side", () => {
    const { container } = renderResult();
    const list = transcriptRows(container);
    expect(list).toHaveLength(3);

    expect(within(list[0]!).getByText("[00:00 – 00:05]")).toBeInTheDocument();
    expect(within(list[2]!).getByText("[00:12 – 01:05]")).toBeInTheDocument();
    expect(within(list[1]!).getByText("Speaker 2")).toHaveClass("speaker-chip", "speaker-chip--2");

    const original = within(list[1]!).getByText(segments[1]!.original_text);
    expect(original).toHaveAttribute("lang", "hi");
    const english = within(list[1]!).getByText("I want a refund for my order.");
    expect(english).toHaveAttribute("lang", "en");

    // Two-column layout: both cells live in the same grid row.
    const body = container.querySelector(".transcript__body");
    expect(body).toHaveClass("transcript--two-col");
    const cells = list[1]!.querySelector(".segment__texts")!.children;
    expect(cells).toHaveLength(2);
    expect(cells[0]).toHaveClass("segment__text--original");
    expect(cells[1]).toHaveClass("segment__text--english");
    expect(screen.getByText("Original (Hindi)")).toBeInTheDocument();
  });

  it("hides the English column with a note when translation is unavailable", () => {
    const noEnglish = segments.map((s) => ({ ...s, english_text: null }));
    const { container } = renderResult({
      source_language: "bhb",
      translation_available: false,
      segments: noEnglish,
    });

    expect(container.querySelector(".transcript__body")).toHaveClass("transcript--one-col");
    expect(container.querySelector(".segment__text--english")).toBeNull();
    expect(screen.queryByText("Translation not available")).not.toBeInTheDocument();
    expect(screen.getByText(/English translation isn't available for Bhili/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy transcript" })).toBeInTheDocument();
  });

  it("shows a muted placeholder when a single segment has no translation", () => {
    const withGap = segments.map((s, i) => (i === 0 ? { ...s, english_text: null } : s));
    const { container } = renderResult({ segments: withGap });
    const first = transcriptRows(container)[0]!;
    expect(within(first).getByText("Translation not available")).toHaveClass("segment__missing");
  });

  it("hides the speaker chip when the speaker is unknown", () => {
    const { container } = renderResult({ segments: segments.map((s) => ({ ...s, speaker: null })), speaker_count: null });
    expect(container.querySelector(".speaker-chip")).toBeNull();
    expect(within(screen.getByRole("region", { name: "Call details" })).queryByText("Speakers")).toBeNull();
  });

  it("filters segments by text in either language and shows the match count", async () => {
    const { container } = renderResult();
    expect(screen.getByText("3 segments")).toBeInTheDocument();

    const filter = screen.getByLabelText("Filter transcript");
    await userEvent.type(filter, "REFUND");
    expect(screen.getByText("1 of 3 segments match")).toBeInTheDocument();
    expect(transcriptRows(container)).toHaveLength(1);
    expect(container.querySelector("mark")).toHaveTextContent("refund");

    await userEvent.clear(filter);
    await userEvent.type(filter, "जाँच");
    expect(screen.getByText("1 of 3 segments match")).toBeInTheDocument();
    expect(transcriptRows(container)[0]).toHaveAttribute("data-segment", "3");

    await userEvent.clear(filter);
    await userEvent.type(filter, "nothing like this");
    expect(screen.getByText("0 of 3 segments match")).toBeInTheDocument();
    expect(screen.getByText("No segments match “nothing like this”.")).toBeInTheDocument();
  });

  it("shows a warning banner and inline segment errors for partial results", () => {
    const partialSegments = segments.map((s, i) =>
      i === 1 ? { ...s, original_text: "", english_text: null, error: "Speech-to-Text timed out" } : s,
    );
    renderResult({
      status: "partial",
      segments: partialSegments,
      warnings: ["1 of 3 pieces failed to transcribe."],
    });

    expect(screen.getByText("Some pieces of this call couldn't be processed.")).toBeInTheDocument();
    expect(screen.getByText("1 of 3 pieces failed to transcribe.")).toBeInTheDocument();
    expect(screen.getByText(/This piece couldn't be fully processed: Speech-to-Text timed out/)).toBeInTheDocument();
    expect(screen.getByText("Partially completed")).toBeInTheDocument();
  });

  it("tells the user the result expired when an export returns 404", async () => {
    const api = createMockApi({
      downloadExport: vi.fn(async () => {
        throw new ApiError(404, "not_found", "Not found");
      }),
    });
    renderResult({}, { api });
    await userEvent.click(screen.getByRole("button", { name: "Export segments CSV" }));
    expect(await screen.findByText(/This result has expired on the server/)).toBeInTheDocument();
    expect(api.downloadExport).toHaveBeenCalledWith("call-123", "segments_csv");
  });

  it("calls onStartOver from the Process another call button", async () => {
    const { onStartOver } = renderResult();
    await userEvent.click(screen.getByRole("button", { name: "Process another call" }));
    expect(onStartOver).toHaveBeenCalled();
  });
});

describe("Result screen audio", () => {
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;

  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:mock-audio");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
  });

  afterEach(() => {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  });

  it("plays the uploaded file, seeks on timestamp click and highlights the playing segment", async () => {
    const file = new File(["audio"], "customer-call.wav", { type: "audio/wav" });
    const { container } = renderResult({}, { audioFile: file });

    const audio = container.querySelector("audio")!;
    expect(audio).toHaveAttribute("src", "blob:mock-audio");

    await userEvent.click(screen.getByRole("button", { name: "[00:05 – 00:12]" }));
    expect(audio.currentTime).toBe(5.2);
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
    expect(transcriptRows(container)[1]).toHaveAttribute("aria-current", "true");
    expect(transcriptRows(container)[0]).not.toHaveAttribute("aria-current");
  });

  it("hides the player with a note when the browser can't play the file", async () => {
    const file = new File(["audio"], "customer-call.wav", { type: "audio/wav" });
    const { container } = renderResult({}, { audioFile: file });

    fireEvent.error(container.querySelector("audio")!);
    expect(await screen.findByText(/This browser can't play this audio format/)).toBeInTheDocument();
    expect(container.querySelector("audio")).toBeNull();
    // Timestamps become plain text once seeking isn't possible.
    expect(screen.queryByRole("button", { name: "[00:00 – 00:05]" })).toBeNull();
  });

  it("shows the AI summary with sentiment, resolution, action items and topics", () => {
    renderResult();
    const summary = screen.getByRole("region", { name: "AI summary" });
    expect(within(summary).getByText(/asked for a refund/)).toBeInTheDocument();
    expect(within(summary).getByText("Partly resolved")).toBeInTheDocument();
    expect(within(summary).getByText("Negative")).toHaveClass("sentiment-chip--negative");
    expect(within(summary).getByText("-0.40")).toBeInTheDocument();
    expect(within(summary).getByText("Agent to confirm the refund by email")).toBeInTheDocument();
    expect(within(summary).getByText("refund")).toHaveClass("topic-chip");
    expect(within(summary).getByText(/Generated by sarvam-105b/)).toBeInTheDocument();
  });

  it("tags only positive and negative segments, and hides the summary without analysis", () => {
    const { container } = renderResult();
    const rows = transcriptRows(container);
    expect(within(rows[1]!).getByText("Negative")).toHaveClass("sentiment-chip");
    expect(rows[0]!.querySelector(".sentiment-chip")).toBeNull();

    const { container: plain } = renderResult({ analysis: null, call_id: "call-plain" });
    expect(plain.querySelector(".call-summary")).toBeNull();
  });
});
