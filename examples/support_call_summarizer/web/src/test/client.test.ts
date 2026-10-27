import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient, errorFromBody, filenameFromContentDisposition } from "../api";
import { config } from "./fixtures";

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

describe("API client", () => {
  it("uses the base URL and sends X-App-Key when configured", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(config));
    const api = createApiClient({ baseUrl: "https://calls.example.com/", appKey: "secret", fetchImpl });

    await expect(api.getConfig()).resolves.toEqual(config);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://calls.example.com/api/config");
    expect((init.headers as Record<string, string>)["X-App-Key"]).toBe("secret");
  });

  it("omits X-App-Key and uses same-origin paths by default", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ languages: [] }));
    const api = createApiClient({ fetchImpl });
    await api.getLanguages();
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/languages");
    expect(init.headers).not.toHaveProperty("X-App-Key");
  });

  it("turns error bodies into ApiError with the server message", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ error: { code: "not_found", message: "Result expired." } }, 404));
    const api = createApiClient({ fetchImpl });
    const err = await api.getCall("abc").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: "not_found", message: "Result expired." });
  });

  it("reports network failures as retryable network errors", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    const api = createApiClient({ fetchImpl });
    const err = (await api.getCall("abc").catch((e: unknown) => e)) as ApiError;
    expect(err.isNetworkError).toBe(true);
    expect(err.isRetryable).toBe(true);
  });

  it("falls back to a generic message for non-JSON error pages", () => {
    const err = errorFromBody(502, "<html>Bad gateway</html>");
    expect(err.code).toBe("http_502");
    expect(err.isRetryable).toBe(true);
    expect(err.message).toMatch(/HTTP 502/);
  });

  it("downloads exports with the app key and the server's filename", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response("a,b\n1,2\n", {
          status: 200,
          headers: { "Content-Disposition": 'attachment; filename="call-abc-segments.csv"' },
        }),
    );
    const api = createApiClient({ appKey: "k", fetchImpl });
    const { blob, filename } = await api.downloadExport("abc", "segments_csv");
    expect(filename).toBe("call-abc-segments.csv");
    expect(await blob.text()).toBe("a,b\n1,2\n");
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/calls/abc/export?format=segments_csv");
    expect((init.headers as Record<string, string>)["X-App-Key"]).toBe("k");
  });
});

describe("filenameFromContentDisposition", () => {
  it.each([
    ['attachment; filename="call.json"', "call.json"],
    ["attachment; filename=call.csv", "call.csv"],
    ["attachment; filename*=UTF-8''%E0%A4%95%E0%A5%89%E0%A4%B2.csv", "कॉल.csv"],
    ['attachment; filename="fallback.csv"; filename*=UTF-8\'\'real.csv', "real.csv"],
    ['attachment; filename="../../etc/passwd"', "passwd"],
    ["attachment", null],
    [null, null],
  ])("parses %s", (header, expected) => {
    expect(filenameFromContentDisposition(header)).toBe(expected);
  });
});
