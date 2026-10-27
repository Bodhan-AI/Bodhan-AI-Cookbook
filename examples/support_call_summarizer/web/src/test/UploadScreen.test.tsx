import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { ApiError } from "../api";
import { UploadScreen } from "../components/UploadScreen";
import { config, createMockApi, fileOfSize, languages } from "./fixtures";

function pickFile(file: File) {
  fireEvent.change(screen.getByLabelText("Call recording"), { target: { files: [file] } });
}

describe("Upload screen", () => {
  it("loads the language list from the API with Auto-detect as the default", async () => {
    const api = createMockApi();
    render(<App api={api} />);

    const select = await screen.findByLabelText("Call language");
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Auto-detect", "Hindi", "Tamil", "Bhili", "Bhojpuri", "English"]);
    expect(select).toHaveValue("");
    expect(screen.getByText(/choosing the language spoken in the call improves accuracy/i)).toBeInTheDocument();
    expect(api.getConfig).toHaveBeenCalled();
    expect(api.getLanguages).toHaveBeenCalled();
  });

  it("shows an error with a retry button when settings fail to load", async () => {
    const api = createMockApi({
      getConfig: vi.fn(async () => {
        throw new ApiError(0, "network_error", "Could not reach the server.");
      }),
    });
    render(<App api={api} />);
    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("accepts only the allowed extensions in the file picker", () => {
    render(<UploadScreen config={config} languages={languages} onSubmit={vi.fn()} />);
    expect(screen.getByLabelText("Call recording")).toHaveAttribute("accept", ".wav,.mp3,.m4a,.ogg");
  });

  it("rejects a file with an unsupported extension", async () => {
    const onSubmit = vi.fn();
    render(<UploadScreen config={config} languages={languages} onSubmit={onSubmit} />);

    pickFile(fileOfSize("notes.txt", 1000, "text/plain"));
    expect(screen.getByRole("alert")).toHaveTextContent("“.txt” files aren't supported. Use one of: .wav, .mp3, .m4a, .ogg.");

    await userEvent.click(screen.getByRole("button", { name: "Process call" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("rejects a file larger than the configured limit", async () => {
    const onSubmit = vi.fn();
    render(<UploadScreen config={config} languages={languages} onSubmit={onSubmit} />);

    pickFile(fileOfSize("long-call.mp3", 150 * 1024 * 1024, "audio/mpeg"));
    expect(screen.getByRole("alert")).toHaveTextContent("This file is 150 MB; the limit is 100 MB.");

    await userEvent.click(screen.getByRole("button", { name: "Process call" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("asks for a file when submitting without one", async () => {
    const onSubmit = vi.fn();
    render(<UploadScreen config={config} languages={languages} onSubmit={onSubmit} />);
    await userEvent.click(screen.getByRole("button", { name: "Process call" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a call recording to process.");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits a valid file with the chosen language and optional details", async () => {
    const onSubmit = vi.fn();
    render(<UploadScreen config={config} languages={languages} onSubmit={onSubmit} />);

    const file = fileOfSize("Call.WAV", 2 * 1024 * 1024);
    pickFile(file);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("Call.WAV")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Call language"), "ta");
    await userEvent.type(screen.getByLabelText(/Ticket ID/), " TCK-9 ");
    await userEvent.type(screen.getByLabelText(/Agent name/), "Ravi");
    await userEvent.click(screen.getByRole("button", { name: "Process call" }));

    expect(onSubmit).toHaveBeenCalledWith(file, { language: "ta", ticketId: "TCK-9", agentName: "Ravi" });
  });

  it("warns that only the original transcript is produced for languages without translation", async () => {
    render(<UploadScreen config={config} languages={languages} onSubmit={vi.fn()} />);
    expect(screen.queryByText(/translation isn't available/i)).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Call language"), "bhb");
    expect(screen.getByText(/English translation isn't available for Bhili yet/)).toBeInTheDocument();
  });

  it("mentions speaker labelling only when diarization is enabled", () => {
    const { rerender } = render(<UploadScreen config={config} languages={languages} onSubmit={vi.fn()} />);
    expect(screen.getByText(/Speakers will be labelled automatically \(up to 8\)/)).toBeInTheDocument();

    rerender(<UploadScreen config={{ ...config, diarization_enabled: false }} languages={languages} onSubmit={vi.fn()} />);
    expect(screen.queryByText(/Speakers will be labelled/)).not.toBeInTheDocument();
  });

  it("shows the server's error message and keeps the chosen file when the upload is rejected", async () => {
    const api = createMockApi({
      uploadCall: vi.fn(async () => {
        throw new ApiError(400, "call_too_long", "This call is longer than 60 minutes.");
      }),
    });
    render(<App api={api} />);
    await screen.findByLabelText("Call language");

    pickFile(fileOfSize("call.wav", 1024));
    await userEvent.click(screen.getByRole("button", { name: "Process call" }));

    expect(await screen.findByText("This call is longer than 60 minutes.")).toBeInTheDocument();
    expect(screen.getByText("call.wav")).toBeInTheDocument();
  });

  it("uploads, stores the call id in the URL and localStorage, and starts polling", async () => {
    const api = createMockApi();
    render(<App api={api} />);
    await screen.findByLabelText("Call language");

    pickFile(fileOfSize("call.wav", 1024));
    await userEvent.click(screen.getByRole("button", { name: "Process call" }));

    expect(await screen.findByText("Transcribing piece 12 of 20")).toBeInTheDocument();
    await waitFor(() => expect(api.getCall).toHaveBeenCalledWith("call-123", expect.anything()));
    expect(new URL(window.location.href).searchParams.get("call")).toBe("call-123");
    expect(window.localStorage.getItem("support-call-summarizer.activeCallId")).toBe("call-123");
  });
});
