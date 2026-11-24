import { vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import FileDrop from "../components/FileDrop";
import Recorder from "../components/Recorder";
import { Alert, ProvenanceBadge } from "../components/ui";
import Layout from "../components/Layout";
import { AuthProvider } from "../lib/auth";
import { ModeProvider } from "../lib/mode";

describe("provenance badges", () => {
  it("labels each kind of content distinctly", () => {
    render(<><ProvenanceBadge value="demo" /><ProvenanceBadge value="live_bodhan" /><ProvenanceBadge value="user_authored" /><ProvenanceBadge value="illustrative" /></>);
    ["DEMO", "LIVE · Bodhan", "Contributor-authored", "ILLUSTRATIVE"].forEach((t) => expect(screen.getByText(t)).toBeInTheDocument());
  });
});

describe("FileDrop validation", () => {
  it("rejects disallowed extensions and oversize files", async () => {
    const onFile = vi.fn();
    render(<FileDrop accept="" extensions={[".png"]} maxMB={1} onFile={onFile} title="Drop" />);
    const input = document.getElementById("file-input-hidden") as HTMLInputElement;
    await userEvent.upload(input, new File(["x"], "evil.exe"), { applyAccept: false });
    expect(screen.getByRole("alert")).toHaveTextContent("not an allowed file type");
    expect(onFile).not.toHaveBeenCalled();
    await userEvent.upload(input, new File(["x"], "ok.png"), { applyAccept: false });
    expect(onFile).toHaveBeenCalledOnce();
  });
});

describe("Recorder", () => {
  it("offers an upload alternative when the browser cannot record", () => {
    render(<Recorder onComplete={() => undefined} />);
    expect(screen.getByText(/Recording is not available/)).toBeInTheDocument();
  });
});

describe("Mode indicator", () => {
  it("always shows Demo or Live and never switches by itself", async () => {
    localStorage.clear();
    render(<MemoryRouter><ModeProvider><AuthProvider><Layout /></AuthProvider></ModeProvider></MemoryRouter>);
    expect(screen.getByTestId("mode-banner")).toHaveTextContent("DEMO MODE");
    await userEvent.click(screen.getByRole("button", { name: "live" }));
    expect(screen.getByTestId("mode-banner")).toHaveTextContent("LIVE MODE");
  });
});

describe("Alert", () => { it("announces errors", () => { render(<Alert kind="error" title="Oops" />); expect(screen.getByRole("alert")).toHaveTextContent("Oops"); }); });
