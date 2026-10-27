import { describe, expect, it } from "vitest";
import { validateFile, fileExtension } from "../lib/files";
import { formatBytes, formatLimitDuration, formatTimeRange, formatTimestamp } from "../lib/format";
import { speakerColorIndex } from "../lib/speakers";
import { buildSteps } from "../lib/steps";
import { activeSegmentNo, filterSegments, transcriptText } from "../lib/transcript";
import { config, makeRecord, segments } from "./fixtures";

describe("formatTimestamp", () => {
  it.each([
    [0, "00:00"],
    [5.9, "00:05"],
    [65.4, "01:05"],
    [599, "09:59"],
    [3599, "59:59"],
    [3600, "1:00:00"],
    [3725, "1:02:05"],
    [36000, "10:00:00"],
    [-3, "00:00"],
    [Number.NaN, "00:00"],
  ])("formats %s seconds as %s", (input, expected) => {
    expect(formatTimestamp(input)).toBe(expected);
  });

  it("formats ranges with an en dash", () => {
    expect(formatTimeRange(5.2, 12.8)).toBe("[00:05 – 00:12]");
  });
});

describe("other formatters", () => {
  it("formats bytes and limits", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(150 * 1024 * 1024)).toBe("150 MB");
    expect(formatLimitDuration(45)).toBe("45 seconds");
    expect(formatLimitDuration(1800)).toBe("30 minutes");
    expect(formatLimitDuration(3600)).toBe("1 hour");
    expect(formatLimitDuration(7200)).toBe("2 hours");
  });
});

describe("validateFile", () => {
  it("accepts allowed extensions case-insensitively", () => {
    expect(fileExtension("Call.WAV")).toBe(".wav");
    expect(validateFile({ name: "Call.WAV", size: 10 }, config)).toBeNull();
  });

  it("rejects missing or unsupported extensions, empty and oversized files", () => {
    expect(validateFile({ name: "recording", size: 10 }, config)).toMatch(/no extension/);
    expect(validateFile({ name: "clip.mp4", size: 10 }, config)).toMatch(/“\.mp4” files aren't supported/);
    expect(validateFile({ name: "call.wav", size: 0 }, config)).toMatch(/empty/);
    expect(validateFile({ name: "call.wav", size: 100 * 1024 * 1024 + 1 }, config)).toMatch(/limit is 100 MB/);
  });
});

describe("buildSteps", () => {
  it("maps backend stages onto the visible steps", () => {
    const states = (stage: Parameters<typeof buildSteps>[0]["stage"], status: Parameters<typeof buildSteps>[0]["status"] = "processing") =>
      buildSteps({ diarizationEnabled: true, stage, status }).map((s) => `${s.key}:${s.state}`);

    expect(states(null, null)[0]).toBe("uploading:current");
    expect(states("queued")).toContain("converting:current");
    expect(states("segmenting")).toContain("transcribing:current");
    expect(states("translating")).toEqual([
      "uploading:complete",
      "converting:complete",
      "diarizing:complete",
      "transcribing:complete",
      "translating:current",
      "done:upcoming",
    ]);
    expect(states("done", "completed").every((s) => s.endsWith(":complete"))).toBe(true);
    expect(states("transcribing", "failed")).toContain("transcribing:failed");
  });

  it("adds the summarizing step when analysis is on", () => {
    const keys = (analysisEnabled: boolean, stage: Parameters<typeof buildSteps>[0]["stage"] = "translating") =>
      buildSteps({ diarizationEnabled: false, analysisEnabled, stage, status: "processing" }).map((s) => s.key);
    expect(keys(false)).not.toContain("analyzing");
    expect(keys(true)).toEqual(["uploading", "converting", "transcribing", "translating", "analyzing", "done"]);
    expect(
      buildSteps({ diarizationEnabled: false, analysisEnabled: true, stage: "analyzing", status: "processing" }).find(
        (s) => s.key === "analyzing",
      )?.state,
    ).toBe("current");
  });
});

describe("transcript helpers", () => {
  it("filters, finds the active segment and builds copy text", () => {
    expect(filterSegments(segments, "  HELLO ")).toHaveLength(1);
    expect(filterSegments(segments, "")).toHaveLength(3);
    expect(activeSegmentNo(segments, 6)).toBe(2);
    expect(activeSegmentNo(segments, 999)).toBeNull();
    expect(transcriptText(makeRecord(), "english").split("\n")[0]).toBe("[00:00 – 00:05] Speaker 1: Hello, how can I help you?");
  });

  it("assigns stable speaker colours", () => {
    expect(speakerColorIndex("Speaker 1")).toBe(1);
    expect(speakerColorIndex("Speaker 8")).toBe(8);
    expect(speakerColorIndex("Speaker 9")).toBe(1);
    expect(speakerColorIndex("Agent")).toBe(speakerColorIndex("Agent"));
  });
});
