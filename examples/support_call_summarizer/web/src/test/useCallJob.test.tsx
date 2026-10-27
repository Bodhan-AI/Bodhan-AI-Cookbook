import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type JobView } from "../api";
import { retryDelay, useCallJob } from "../hooks/useCallJob";
import { makeCompletedJob, makeJob } from "./fixtures";

const fast = { pollIntervalMs: 10, maxBackoffMs: 40 };

describe("useCallJob", () => {
  it("polls until the job reaches a terminal status", async () => {
    const responses: JobView[] = [makeJob(), makeJob(), makeCompletedJob()];
    const getCall = vi.fn(async () => responses.shift() ?? makeCompletedJob());
    const api = { getCall };
    const { result } = renderHook(() => useCallJob(api, "call-123", fast));

    await waitFor(() => expect(result.current.job?.status).toBe("completed"));
    const calls = getCall.mock.calls.length;
    await new Promise((r) => setTimeout(r, 50));
    expect(getCall.mock.calls.length).toBe(calls);
  });

  it("keeps the last job and flags connection loss on network errors, then recovers", async () => {
    let call = 0;
    const getCall = vi.fn(async () => {
      call += 1;
      if (call === 1) return makeJob();
      if (call <= 3) throw new ApiError(0, "network_error", "offline");
      return makeCompletedJob();
    });
    const api = { getCall };
    const { result } = renderHook(() => useCallJob(api, "call-123", fast));

    await waitFor(() => expect(result.current.connectionLost).toBe(true));
    expect(result.current.job?.progress.message).toBe("Transcribing piece 12 of 20");

    await waitFor(() => expect(result.current.job?.status).toBe("completed"));
    expect(result.current.connectionLost).toBe(false);
  });

  it("reports not found on 404 and stops polling", async () => {
    const getCall = vi.fn(async () => {
      throw new ApiError(404, "not_found", "gone");
    });
    const api = { getCall };
    const { result } = renderHook(() => useCallJob(api, "missing", fast));
    await waitFor(() => expect(result.current.notFound).toBe(true));
    await new Promise((r) => setTimeout(r, 40));
    expect(getCall).toHaveBeenCalledTimes(1);
  });

  it("re-polls immediately when the device comes back online", async () => {
    const getCall = vi.fn(async () => makeJob());
    const api = { getCall };
    renderHook(() => useCallJob(api, "call-123", { pollIntervalMs: 60_000 }));
    await waitFor(() => expect(getCall).toHaveBeenCalledTimes(1));

    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    await waitFor(() => expect(getCall).toHaveBeenCalledTimes(2));
  });

  it("caps the retry backoff", () => {
    expect(retryDelay(1, 2000, 15000)).toBe(4000);
    expect(retryDelay(2, 2000, 15000)).toBe(8000);
    expect(retryDelay(5, 2000, 15000)).toBe(15000);
  });
});
