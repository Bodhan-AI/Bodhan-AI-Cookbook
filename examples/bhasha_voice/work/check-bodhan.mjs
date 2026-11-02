// Provider calls are stubbed. This suite never loads .env.local or spends API credit.
import assert from "node:assert/strict";
import test from "node:test";
import { transcribeWav } from "../src/server/bodhan/transcribe.mjs";
import { handleAudioUpload } from "../src/server/audio/handleAudioUpload.mjs";
import { AudioError } from "../src/server/audio/prepareAudio.mjs";

const apiKey = "test-only-server-key";
const wav = Buffer.from("test WAV bytes");

test("sends one authenticated multipart WAV request and returns plain transcript text", async () => {
  let calls = 0;
  const text = await transcribeWav(wav, "hi", {
    apiKey,
    fetchImpl: async (url, options) => {
      calls++;
      assert.equal(url, "https://api.bodhan.ai/v1/audio/transcriptions");
      assert.equal(options.method, "POST");
      assert.equal(options.headers.Authorization, `Bearer ${apiKey}`);
      assert.equal(options.headers["Content-Type"], undefined);
      assert.equal(options.redirect, "error");
      assert.equal(options.cache, "no-store");
      assert.equal(options.body.get("model"), "indic-transcribe");
      assert.equal(options.body.get("language"), "hi");
      const file = options.body.get("file");
      assert.equal(file.name, "recording.wav");
      assert.equal(file.type, "audio/wav");
      assert.deepEqual(Buffer.from(await file.arrayBuffer()), wav);
      return Response.json({ text: "  नमस्ते  " });
    },
  });
  assert.equal(text, "नमस्ते");
  assert.equal(calls, 1);
});

test("missing credentials and invalid languages never call the provider", async () => {
  const fetchImpl = () => { assert.fail("must not call the provider"); };
  for (const invalidKey of ["", "   ", "key\ninvalid"]) {
    await assert.rejects(transcribeWav(wav, "en", { apiKey: invalidKey, fetchImpl }), { code: "TRANSCRIPTION_NOT_CONFIGURED", status: 503 });
  }
  await assert.rejects(transcribeWav(wav, "fr", { apiKey, fetchImpl }), { code: "INVALID_LANGUAGE", status: 400 });
});

test("provider failures use safe messages and never retry paid requests", async () => {
  for (const [status, code, expectedStatus] of [
    [401, "PROVIDER_AUTH_FAILED", 502], [403, "PROVIDER_AUTH_FAILED", 502],
    [402, "PROVIDER_CREDIT_EXHAUSTED", 503], [429, "PROVIDER_RATE_LIMITED", 429],
    [408, "PROVIDER_TIMEOUT", 504], [504, "PROVIDER_TIMEOUT", 504],
    [415, "PROVIDER_REQUEST_FAILED", 502], [500, "PROVIDER_REQUEST_FAILED", 502],
  ]) {
    let calls = 0;
    await assert.rejects(transcribeWav(wav, "en", {
      apiKey,
      fetchImpl: async () => {
        calls++;
        return Response.json({ error: { message: `internal detail ${apiKey}` } }, {
          status, headers: { "x-request-id": "request-123", "retry-after": "12" },
        });
      },
    }), (error) => {
      assert.equal(error.code, code);
      assert.equal(error.status, expectedStatus);
      assert.equal(error.requestId, "request-123");
      assert.equal(error.retryAfterSeconds, 12);
      assert.ok(!error.message.includes(apiKey));
      return true;
    });
    assert.equal(calls, 1);
  }
});

test("empty, malformed, oversized and error-shaped success responses are rejected", async () => {
  for (const response of [
    Response.json({ text: "" }), Response.json({ text: "  " }), Response.json({ text: 1 }),
    Response.json(null), Response.json({ error: {}, text: "unusable" }),
    new Response("<html>gateway error</html>"),
    Response.json({ text: "x".repeat(70 * 1024) }),
  ]) {
    await assert.rejects(transcribeWav(wav, "en", { apiKey, fetchImpl: async () => response }), {
      code: "INVALID_PROVIDER_RESPONSE", status: 502,
    });
  }
});

test("network errors cannot expose raw fetch errors or credentials", async () => {
  await assert.rejects(transcribeWav(wav, "en", {
    apiKey, fetchImpl: async () => { throw new Error(`network failure ${apiKey}`); },
  }), (error) => {
    assert.equal(error.code, "PROVIDER_UNAVAILABLE");
    assert.ok(!error.message.includes(apiKey));
    return true;
  });
});

test("an already cancelled upload makes no provider request", async () => {
  await assert.rejects(transcribeWav(wav, "en", {
    apiKey, signal: AbortSignal.abort(), fetchImpl: () => assert.fail("must not fetch"),
  }), { code: "PROCESSING_TIMEOUT", status: 408 });
});

test("cancellation during a request and provider deadlines return distinct errors", async () => {
  for (const timedOut of [false, true]) {
    const controller = new AbortController();
    await assert.rejects(transcribeWav(wav, "en", {
      apiKey,
      ...(timedOut ? { timeoutSignal: controller.signal } : { signal: controller.signal }),
      fetchImpl: async (_, options) => {
        controller.abort();
        options.signal.throwIfAborted();
      },
    }), { code: timedOut ? "PROVIDER_TIMEOUT" : "PROCESSING_TIMEOUT", status: timedOut ? 504 : 408 });
  }
});

test("a late successful response after cancellation is not returned", async () => {
  const controller = new AbortController();
  await assert.rejects(transcribeWav(wav, "en", {
    apiKey, signal: controller.signal,
    fetchImpl: async () => { controller.abort(); return Response.json({ text: "late transcript" }); },
  }), { code: "PROCESSING_TIMEOUT" });
});

test("unsafe correlation IDs are omitted and retry delays are bounded", async () => {
  await assert.rejects(transcribeWav(wav, "en", {
    apiKey,
    fetchImpl: async () => new Response("raw error", {
      status: 429, headers: { "x-request-id": apiKey, "retry-after": "999999" },
    }),
  }), (error) => {
    assert.equal(error.requestId, undefined);
    assert.equal(error.retryAfterSeconds, 3600);
    return true;
  });
});

function validUpload() {
  // A 0.1-second PCM16 WAV fixture for the actual conversion stage.
  const bytes = Buffer.alloc(3244);
  bytes.write("RIFF", 0); bytes.writeUInt32LE(3236, 4); bytes.write("WAVEfmt ", 8);
  bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(1, 22);
  bytes.writeUInt32LE(16000, 24); bytes.writeUInt32LE(32000, 28);
  bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34);
  bytes.write("data", 36); bytes.writeUInt32LE(3200, 40);
  const form = new FormData();
  form.set("file", new File([bytes], "input.wav", { type: "audio/wav" }));
  form.set("language", "en");
  return new Request("http://localhost/api/bhasha/transcribe", { method: "POST", body: form });
}

test("upload pipeline passes converted WAV and returns only the transcript and metadata", async () => {
  const response = await handleAudioUpload(validUpload(), {
    transcribe: async (audio, language, { signal }) => {
      assert.equal(audio.toString("ascii", 0, 4), "RIFF");
      assert.equal(language, "en");
      assert.equal(signal.aborted, false);
      return "The pipeline works.";
    },
  });
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.status, "transcribed");
  assert.equal(result.text, "The pipeline works.");
  assert.equal(result.audio.durationSeconds, 0.1);
  assert.ok(!JSON.stringify(result).includes(apiKey));
});

test("upload pipeline preserves safe provider IDs and Retry-After headers", async () => {
  const response = await handleAudioUpload(validUpload(), {
    transcribe: async () => {
      const error = new AudioError("PROVIDER_RATE_LIMITED", "Please try again later.", 429);
      error.requestId = "request-123";
      error.retryAfterSeconds = 10;
      throw error;
    },
  });
  assert.equal(response.status, 429);
  assert.equal(response.headers.get("retry-after"), "10");
  const result = await response.json();
  assert.equal(result.error.requestId, "request-123");
  assert.equal(result.error.retryAfterSeconds, 10);
});
