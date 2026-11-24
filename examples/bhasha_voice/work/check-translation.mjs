import assert from "node:assert/strict";
import test from "node:test";
import { translateText } from "../src/server/bodhan/translate.mjs";
import { handleTranslation } from "../src/server/bodhan/handleTranslation.mjs";
import { requestTranslation } from "../packages/bhashavoice/src/translateText.mjs";

const apiKey = "test-key-only";
const complete = (overrides = {}) => ({ translation: "नमस्ते", target_language: "hi", truncated: false, ...overrides });
const chat = (content = "नमस्ते", finishReason = "stop") => ({ choices: [{ message: { role: "assistant", content }, finish_reason: finishReason }] });
const request = (body, options = {}) => new Request("http://localhost/api/bhasha/translate", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), ...options,
});

test("provider sends only reviewed text and target language, with server authentication", async () => {
  let calls = 0;
  const result = await translateText("  Hello.\nHello again.  ", "hi", { apiKey, fetchImpl: async (url, options) => {
    calls++;
    assert.equal(url, "https://api.bodhan.ai/v1/chat/completions");
    assert.equal(options.headers.Authorization, `Bearer ${apiKey}`);
    assert.equal(options.redirect, "error");
    assert.equal(options.cache, "no-store");
    assert.deepEqual(JSON.parse(options.body), { model: "indic-translate", messages: [{ role: "user", content: "  Hello.\nHello again.  " }], target_language_code: "hi" });
    return Response.json(chat());
  } });
  assert.equal(result, "नमस्ते");
  assert.equal(calls, 1);
});

test("invalid text, target and missing credentials never call the provider", async () => {
  const fetchImpl = () => { assert.fail("Unexpected paid request"); };
  for (const text of [null, "", " \n ", "a".repeat(2001)]) {
    await assert.rejects(translateText(text, "hi", { apiKey, fetchImpl }), { code: "INVALID_TRANSLATION_TEXT" });
  }
  await assert.rejects(translateText("Hello", "fr", { apiKey, fetchImpl }), { code: "INVALID_TARGET_LANGUAGE" });
  for (const key of ["", "\nsecret"]) {
    await assert.rejects(translateText("Hello", "hi", { apiKey: key, fetchImpl }), { code: "TRANSLATION_NOT_CONFIGURED" });
  }
});

test("incomplete, missing and oversized responses cannot become translations", async () => {
  const responses = [
    Response.json({ ...chat(), truncated: true }),
    Response.json(chat("cut off", "length")),
    Response.json(chat("unknown completion", null)),
    Response.json(chat(" ")),
    Response.json({ ...chat(), error: { message: "private" } }),
    Response.json(null), new Response("invalid json"),
    Response.json(chat("a".repeat(65536))),
  ];
  for (const response of responses) {
    await assert.rejects(translateText("Hello", "hi", { apiKey, fetchImpl: async () => response }), (error) => {
      assert.ok(["TRANSLATION_INCOMPLETE", "INVALID_TRANSLATION_RESPONSE"].includes(error.code));
      assert.ok(!error.message.includes("private"));
      return true;
    });
  }
});

test("provider failures are safe, preserve backoff and never retry", async () => {
  for (const [status, code] of [[401, "TRANSLATION_AUTH_FAILED"], [402, "TRANSLATION_CREDIT_EXHAUSTED"], [429, "TRANSLATION_RATE_LIMITED"], [504, "TRANSLATION_TIMEOUT"], [500, "TRANSLATION_FAILED"]]) {
    let calls = 0;
    await assert.rejects(translateText("Hello", "hi", { apiKey, fetchImpl: async () => {
      calls++;
      return new Response("private upstream data", { status, headers: { "Retry-After": "5", "x-request-id": "req-123" } });
    } }), (error) => {
      assert.equal(error.code, code);
      assert.equal(error.requestId, "req-123");
      assert.equal(error.retryAfterSeconds, 5);
      assert.ok(!error.message.includes("private"));
      return true;
    });
    assert.equal(calls, 1);
  }
});

test("sensitive request IDs and network details are never forwarded", async () => {
  await assert.rejects(translateText("Hello", "hi", { apiKey, fetchImpl: async () => new Response("private", { status: 401, headers: { "x-request-id": apiKey } }) }), (error) => {
    assert.equal(error.requestId, undefined);
    return true;
  });
  await assert.rejects(translateText("Hello", "hi", { apiKey, fetchImpl: async () => { throw new TypeError("secret network details"); } }), { code: "TRANSLATION_UNAVAILABLE" });
});

test("cancellation rejects late responses and timeout cancels a stalled response body", async () => {
  const caller = new AbortController();
  await assert.rejects(translateText("Hello", "hi", { apiKey, signal: caller.signal, fetchImpl: async () => {
    caller.abort();
    return Response.json(chat());
  } }), { code: "TRANSLATION_CANCELLED" });
  const timeout = new AbortController();
  let cancelled = false;
  const pending = translateText("Hello", "hi", { apiKey, timeoutSignal: timeout.signal, fetchImpl: async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('{"translation":')); },
    cancel() { cancelled = true; },
  })) });
  await new Promise((resolve) => setImmediate(resolve));
  timeout.abort();
  await assert.rejects(pending, { code: "TRANSLATION_TIMEOUT" });
  assert.equal(cancelled, true);
});

test("endpoint returns a complete translation with no cache and passes abort signal", async () => {
  const response = await handleTranslation(request({ text: "Hello", target_language: "hi" }), { translate: async (text, target, { signal }) => {
    assert.equal(text, "Hello");
    assert.equal(target, "hi");
    assert.equal(signal.aborted, false);
    return "नमस्ते";
  } });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), { status: "translated", ...complete() });
});

test("endpoint rejects invalid JSON, content type, text and target before translation", async () => {
  const translate = () => { assert.fail("Unexpected provider request"); };
  for (const req of [
    request(null), request({ text: " ", target_language: "hi" }),
    request({ text: "a".repeat(2001), target_language: "hi" }),
    request({ text: "Hello", target_language: "fr" }),
    request({}, { body: "{" }), request({}, { headers: { "Content-Type": "text/plain" } }),
  ]) {
    const response = await handleTranslation(req, { translate });
    assert.ok([400, 415].includes(response.status));
  }
});

test("endpoint bounds a streamed body even without Content-Length", async () => {
  const response = await handleTranslation(request({}, { body: new ReadableStream({ start(controller) {
    controller.enqueue(new Uint8Array(33000)); controller.close();
  } }), duplex: "half" }), { translate: () => { assert.fail("Unexpected provider request"); } });
  assert.equal(response.status, 413);
});

test("endpoint cancels a stalled upload and releases its concurrency slot", async () => {
  const controller = new AbortController();
  const pending = handleTranslation(request({}, { body: new ReadableStream(), duplex: "half", signal: controller.signal }));
  controller.abort();
  assert.equal((await pending).status, 408);
});

test("endpoint limits concurrent requests, then recovers after they finish", async () => {
  const resolves = [];
  const translate = () => new Promise((resolve) => resolves.push(resolve));
  const first = handleTranslation(request({ text: "Hello", target_language: "hi" }), { translate });
  const second = handleTranslation(request({ text: "Hello", target_language: "hi" }), { translate });
  await new Promise((resolve) => setImmediate(resolve));
  const busy = await handleTranslation(request({ text: "Hello", target_language: "hi" }), { translate });
  assert.equal(busy.status, 429);
  assert.equal(busy.headers.get("retry-after"), "1");
  resolves.forEach((resolve) => resolve("नमस्ते"));
  assert.equal((await first).status, 200);
  assert.equal((await second).status, 200);
  assert.equal((await handleTranslation(request({ text: "Hello", target_language: "hi" }), { translate: async () => "नमस्ते" })).status, 200);
});

test("client sends reviewed text to the host backend without API credentials", async () => {
  const translated = await requestTranslation("Corrected source", "hi", { apiBaseUrl: "/custom/", fetchImpl: async (url, options) => {
    assert.equal(url, "/custom/translate");
    assert.deepEqual(options.headers, { "Content-Type": "application/json" });
    assert.equal(options.credentials, "same-origin");
    assert.deepEqual(JSON.parse(options.body), { text: "Corrected source", target_language: "hi" });
    return Response.json({ status: "translated", ...complete() });
  } });
  assert.equal(translated, "नमस्ते");
});

test("client rejects incomplete, empty, unreadable and wrong-target output", async () => {
  for (const response of [
    Response.json({ status: "translated", ...complete({ truncated: true }) }),
    Response.json({ status: "translated", ...complete({ target_language: "en" }) }),
    Response.json({ status: "translated", ...complete({ translation: " " }) }),
    Response.json(null), new Response("<html>error</html>"),
  ]) await assert.rejects(requestTranslation("Hello", "hi", { fetchImpl: async () => response }));
});

test("client respects backoff, hides provider details and does not retry", async () => {
  let calls = 0;
  await assert.rejects(requestTranslation("Hello", "hi", { fetchImpl: async () => {
    calls++;
    return Response.json({ error: { code: "TRANSLATION_RATE_LIMITED", message: "private data" } }, { status: 429, headers: { "Retry-After": "10" } });
  } }), (error) => {
    assert.equal(error.retryAfterSeconds, 10);
    assert.ok(!error.message.includes("private"));
    return true;
  });
  assert.equal(calls, 1);
});

test("client rejects late cancelled results and provides safe network recovery", async () => {
  const controller = new AbortController();
  await assert.rejects(requestTranslation("Hello", "hi", { signal: controller.signal, fetchImpl: async () => {
    controller.abort(); return Response.json({ status: "translated", ...complete() });
  } }));
  await assert.rejects(requestTranslation("Hello", "hi", { fetchImpl: async () => { throw new TypeError("private"); } }), /original text is still available/);
});
