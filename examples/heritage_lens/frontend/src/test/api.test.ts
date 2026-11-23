import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, tokenStore } from "../lib/api";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
beforeEach(() => { tokenStore.clear(); });
afterEach(() => { vi.restoreAllMocks(); });

describe("api client", () => {
  it("sends the bearer token on authenticated calls only", async () => {
    tokenStore.set("tok123");
    const f = vi.fn().mockResolvedValue(json(200, { id: 1 })); vi.stubGlobal("fetch", f);
    await api.me();
    expect((f.mock.calls[0][1].headers as Headers).get("Authorization")).toBe("Bearer tok123");
    f.mockResolvedValue(json(200, { access_token: "x", user: {} }));
    await api.login({ email: "a@b.co", password: "pw" });
    expect((f.mock.calls[1][1].headers as Headers).get("Authorization")).toBeNull();
  });
  it("turns the backend error envelope into ApiError with request id", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(503, { error: { code: "ai_service_not_configured", message: "Service not configured", request_id: "r-9" } })));
    const e = await api.translate({ text: "x", target_language: "te" }).catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toMatchObject({ status: 503, code: "ai_service_not_configured", requestId: "r-9" });
    expect(e.message).toContain("not configured");
  });
  it("includes field details for validation errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(422, { error: { code: "validation_failed", message: "Some fields are missing or invalid.", details: [{ loc: ["body", "title"], message: "Field required" }] } })));
    const e = await api.createStory({}).catch((x) => x);
    expect(e.message).toContain("title: Field required");
  });
  it("reports an unreachable server clearly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const e = await api.stories({}).catch((x) => x);
    expect(e).toMatchObject({ status: 0, code: "network_error" });
  });
  it("clears the session signal on 401", async () => {
    tokenStore.set("old");
    const handler = vi.fn(); window.addEventListener("hl:unauthorized", handler);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(401, { error: { code: "authentication_failed", message: "expired" } })));
    await api.me().catch(() => undefined);
    expect(handler).toHaveBeenCalled();
  });
  it("only ever calls the HeritageLens /api, never Bodhan", async () => {
    const f = vi.fn().mockImplementation(() => Promise.resolve(json(200, {}))); vi.stubGlobal("fetch", f);
    await api.translate({ text: "x", target_language: "te" }); await api.transliterate({ text: "x", language: "te", script: "roman" });
    await api.ocr({ document_id: 1 }); await api.transcribe({ audio_id: 1, language: "te" }); await api.speak({ text: "x", language: "te", voice: "Sravani" });
    for (const [url] of f.mock.calls) { expect(String(url)).toMatch(/^\/api\/ai\//); expect(String(url)).not.toContain("bodhan"); }
  });
});
