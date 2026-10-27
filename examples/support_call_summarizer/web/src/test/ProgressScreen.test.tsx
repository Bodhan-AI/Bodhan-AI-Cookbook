import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { ApiError } from "../api";
import { ProgressScreen } from "../components/ProgressScreen";
import { createMockApi, makeCompletedJob, makeJob } from "./fixtures";

const noop = () => undefined;

function stepNames() {
  return within(screen.getByRole("list", { name: "Processing steps" }))
    .getAllByRole("listitem")
    .map((li) => li.textContent?.replace(/\s*\(.*\)$/, "").replace(/^[\d✓!]+/, ""));
}

describe("Progress screen", () => {
  it("shows the backend progress message, a determinate bar and the current step", () => {
    render(<ProgressScreen fileName="call.wav" diarizationEnabled uploadFraction={null} job={makeJob()} onStartOver={noop} />);

    expect(screen.getByText("Transcribing piece 12 of 20")).toBeInTheDocument();
    expect(screen.getByText("Transcribing piece 12 of 20")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("progressbar", { name: "Processing progress" })).toHaveAttribute("aria-valuenow", "60");

    const current = screen.getByText("Transcribing").closest("li");
    expect(current).toHaveAttribute("aria-current", "step");
    expect(stepNames()).toEqual(["Uploading", "Converting", "Identifying speakers", "Transcribing", "Translating", "Done"]);
  });

  it("omits the speaker step when diarization is disabled", () => {
    render(<ProgressScreen fileName={null} diarizationEnabled={false} uploadFraction={null} job={makeJob()} onStartOver={noop} />);
    expect(stepNames()).toEqual(["Uploading", "Converting", "Transcribing", "Translating", "Done"]);
  });

  it("shows upload percentage while uploading", () => {
    render(<ProgressScreen fileName="call.wav" diarizationEnabled uploadFraction={0.42} job={null} onCancelUpload={noop} onStartOver={noop} />);
    expect(screen.getByText("Uploading call.wav…")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Upload progress" })).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByText("Uploading").closest("li")).toHaveAttribute("aria-current", "step");
  });

  it("shows the job error and a start-over button when processing failed", async () => {
    const onStartOver = vi.fn();
    const job = makeJob({
      status: "failed",
      progress: { stage: "transcribing", done: 3, total: 20, message: "Transcribing piece 3 of 20" },
      error: { code: "stt_failed", message: "Speech-to-Text is unavailable right now." },
    });
    render(<ProgressScreen fileName="call.wav" diarizationEnabled uploadFraction={null} job={job} onStartOver={onStartOver} />);

    expect(screen.getByRole("heading", { name: "Processing failed" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Speech-to-Text is unavailable right now.");
    await userEvent.click(screen.getByRole("button", { name: "Start over" }));
    expect(onStartOver).toHaveBeenCalledTimes(1);
  });

  it("explains an expired or unknown result", () => {
    render(<ProgressScreen fileName={null} diarizationEnabled uploadFraction={null} job={null} notFound onStartOver={noop} />);
    expect(screen.getByText("This result has expired or was not found.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start over" })).toBeInTheDocument();
  });

  it("keeps the job view and shows a retry notice when the connection drops", async () => {
    const onRetryNow = vi.fn();
    render(
      <ProgressScreen
        fileName="call.wav"
        diarizationEnabled
        uploadFraction={null}
        job={makeJob()}
        connectionLost
        onRetryNow={onRetryNow}
        onStartOver={noop}
      />,
    );
    expect(screen.getByText("Connection lost — retrying…")).toBeInTheDocument();
    expect(screen.getByText("Transcribing piece 12 of 20")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry now" }));
    expect(onRetryNow).toHaveBeenCalled();
  });
});

describe("Resuming a call", () => {
  it("resumes polling from ?call= in the URL and shows the result when complete", async () => {
    window.history.replaceState(null, "", "/?call=call-777");
    const api = createMockApi({ getCall: vi.fn(async () => makeCompletedJob({ call_id: "call-777" })) });
    render(<App api={api} />);

    expect(await screen.findByRole("heading", { name: "Call summary" })).toBeInTheDocument();
    expect(api.getCall).toHaveBeenCalledWith("call-777", expect.anything());
    expect(screen.getByText(/Audio playback is only available in the browser session/)).toBeInTheDocument();
  });

  it("resumes from localStorage when the URL has no call id", async () => {
    window.localStorage.setItem("support-call-summarizer.activeCallId", "call-555");
    const api = createMockApi();
    render(<App api={api} />);

    expect(await screen.findByText("Transcribing piece 12 of 20")).toBeInTheDocument();
    expect(api.getCall).toHaveBeenCalledWith("call-555", expect.anything());
    expect(new URL(window.location.href).searchParams.get("call")).toBe("call-555");
  });

  it("shows the expired message on 404 and starting over clears the saved call", async () => {
    window.history.replaceState(null, "", "/?call=gone");
    const api = createMockApi({
      getCall: vi.fn(async () => {
        throw new ApiError(404, "not_found", "Not found");
      }),
    });
    render(<App api={api} />);

    expect(await screen.findByText("This result has expired or was not found.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start over" }));

    expect(await screen.findByRole("button", { name: "Process call" })).toBeInTheDocument();
    expect(window.location.search).toBe("");
    expect(window.localStorage.getItem("support-call-summarizer.activeCallId")).toBeNull();
  });

  it("goes straight to the upload screen when a remembered call has expired", async () => {
    window.localStorage.setItem("support-call-summarizer.activeCallId", "stale");
    const api = createMockApi({
      getCall: vi.fn(async () => {
        throw new ApiError(404, "not_found", "Not found");
      }),
    });
    render(<App api={api} />);

    expect(await screen.findByRole("button", { name: "Process call" })).toBeInTheDocument();
    expect(screen.queryByText("This result has expired or was not found.")).not.toBeInTheDocument();
    expect(window.localStorage.getItem("support-call-summarizer.activeCallId")).toBeNull();
  });
});
