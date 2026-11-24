import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { transcribeAudio } from "../packages/bhashavoice/src/transcribeAudio.mjs";

const audio = new Blob(["synthetic recording"], { type: "audio/webm;codecs=opus" });

test("client sends audio and language to the configured backend without credentials", async () => {
  const text = await transcribeAudio(audio, "hi", {
    apiBaseUrl: "/custom/bhasha/",
    fetchImpl: async (url, options) => {
      assert.equal(url, "/custom/bhasha/transcribe");
      assert.equal(options.method, "POST");
      assert.equal(options.headers, undefined);
      assert.equal(options.credentials, "same-origin");
      assert.equal(options.body.get("language"), "hi");
      assert.equal(options.body.get("file").type, audio.type);
      assert.equal(await options.body.get("file").text(), "synthetic recording");
      return Response.json({ status: "transcribed", text: "नमस्ते" });
    },
  });
  assert.equal(text, "नमस्ते");
});

test("client rejects missing transcripts and unreadable responses", async () => {
  for (const response of [
    new Response("<html>error</html>"), Response.json({ status: "transcribed", text: " " }),
    Response.json({ status: "audio_prepared" }), Response.json(null),
  ]) {
    await assert.rejects(transcribeAudio(audio, "en", { fetchImpl: async () => response }));
  }
});

test("client uses safe errors, respects server backoff, and never retries automatically", async () => {
  let calls = 0;
  await assert.rejects(transcribeAudio(audio, "en", {
    fetchImpl: async () => {
      calls++;
      return Response.json({ error: { code: "PROVIDER_RATE_LIMITED", message: "private upstream details", retryAfterSeconds: 10 } }, { status: 429 });
    },
  }), (error) => {
    assert.equal(error.retryAfterSeconds, 10);
    assert.ok(!error.message.includes("private upstream"));
    return true;
  });
  assert.equal(calls, 1);
});

test("client turns network errors into a recovery message", async () => {
  await assert.rejects(transcribeAudio(audio, "en", {
    fetchImpl: async () => { throw new TypeError("private fetch failure"); },
  }), /recording is still available/);
});

test("client does not accept a response after cancellation", async () => {
  const controller = new AbortController();
  await assert.rejects(transcribeAudio(audio, "en", {
    signal: controller.signal,
    fetchImpl: async () => { controller.abort(); return Response.json({ status: "transcribed", text: "late" }); },
  }));
});

const source = await readFile(new URL("../packages/bhashavoice/src/useTranscription.js", import.meta.url), "utf8");

// Hook lifecycle simulation, with explicit rerenders and effect cleanup.
async function harness(send) {
  const slots = [];
  const effects = new Map();
  const timers = new Map();
  let cursor = 0;
  let args = [audio, "en", "/api/bhasha"];
  let pendingEffects = [];
  let timerId = 0;
  const context = vm.createContext({
    AbortController,
    setTimeout(fn, delay) { timers.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  });
  const react = new vm.SyntheticModule(["useState", "useRef", "useEffect"], function () {
    this.setExport("useState", (initial) => {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value; }];
    });
    this.setExport("useRef", (initial) => {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    });
    this.setExport("useEffect", (effect, deps) => {
      const index = cursor++;
      const previous = effects.get(index);
      if (!previous || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
        pendingEffects.push(() => {
          previous?.cleanup?.();
          effects.set(index, { deps, cleanup: effect() });
        });
      }
    });
  }, { context });
  const client = new vm.SyntheticModule(["transcribeAudio"], function () { this.setExport("transcribeAudio", send); }, { context });
  const hook = new vm.SourceTextModule(source, { context });
  await hook.link((name) => name === "react" ? react : client);
  await hook.evaluate();
  return {
    render(...nextArgs) {
      if (nextArgs.length) args = nextArgs;
      cursor = 0;
      const result = hook.namespace.default(...args);
      const queue = pendingEffects;
      pendingEffects = [];
      queue.forEach((effect) => effect());
      return result;
    },
    finishBackoff() {
      for (const [id, timer] of [...timers]) { timers.delete(id); timer.fn(); }
    },
    unmount() { for (const effect of effects.values()) effect.cleanup?.(); },
  };
}

test("hook starts only on request and prevents concurrent or duplicate successful submissions", async () => {
  let calls = 0;
  let resolve;
  const h = await harness(() => { calls++; return new Promise((r) => { resolve = r; }); });
  const original = h.render();
  assert.equal(calls, 0);
  const pending = original.transcribe();
  await original.transcribe();
  assert.equal(h.render().status, "loading");
  assert.equal(calls, 1);
  resolve("The transcript.");
  await pending;
  assert.equal(h.render().text, "The transcript.");
  await original.transcribe();
  assert.equal(calls, 1);
  h.unmount();
});

test("hook preserves its source after failure and allows an explicit retry", async () => {
  let calls = 0;
  const h = await harness(async (sourceAudio) => {
    assert.equal(sourceAudio, audio);
    if (++calls === 1) throw new Error("Please try again.");
    return "Recovered transcript.";
  });
  await h.render().transcribe();
  assert.equal(h.render().status, "error");
  await h.render().transcribe();
  assert.equal(h.render().status, "success");
  assert.equal(h.render().text, "Recovered transcript.");
  h.unmount();
});

test("discard aborts a pending request and ignores its late result", async () => {
  let resolve;
  let signal;
  const h = await harness((_, __, options) => {
    signal = options.signal;
    return new Promise((r) => { resolve = r; });
  });
  const pending = h.render().transcribe();
  assert.equal(h.render(null, "en", "/api/bhasha").status, "idle");
  assert.equal(signal.aborted, true);
  resolve("Late transcript.");
  await pending;
  assert.equal(h.render().text, "");
  h.unmount();
});

test("changing language or backend invalidates a completed transcript", async () => {
  const h = await harness(async (_, language) => `${language} transcript`);
  await h.render().transcribe();
  assert.equal(h.render(audio, "hi", "/api/bhasha").text, "");
  await h.render().transcribe();
  assert.equal(h.render().text, "hi transcript");
  assert.equal(h.render(audio, "hi", "/other/backend").text, "");
  h.unmount();
});

test("late results from an older recording cannot replace the newer transcript", async () => {
  let resolveOld;
  let calls = 0;
  const h = await harness(() => ++calls === 1 ? new Promise((r) => { resolveOld = r; }) : Promise.resolve("New transcript."));
  const old = h.render().transcribe();
  await h.render(new Blob(["new audio"]), "en", "/api/bhasha").transcribe();
  resolveOld("Old transcript.");
  await old;
  assert.equal(h.render().text, "New transcript.");
  h.unmount();
});

test("backoff blocks manual retry until the delay expires and never sends automatically", async () => {
  let calls = 0;
  const h = await harness(async () => {
    if (++calls === 1) {
      const error = new Error("Service busy.");
      error.retryAfterSeconds = 10;
      throw error;
    }
    return "Recovered.";
  });
  const original = h.render();
  await original.transcribe();
  assert.equal(h.render().retryAfterSeconds, 10);
  await original.transcribe();
  assert.equal(calls, 1);
  h.finishBackoff();
  assert.equal(calls, 1);
  assert.equal(h.render().retryAfterSeconds, 0);
  await h.render().transcribe();
  assert.equal(calls, 2);
  h.unmount();
});

test("unmount aborts transcription and suppresses a late response", async () => {
  let signal;
  let resolve;
  const h = await harness((_, __, options) => {
    signal = options.signal;
    return new Promise((r) => { resolve = r; });
  });
  const pending = h.render().transcribe();
  h.unmount();
  assert.equal(signal.aborted, true);
  resolve("Late text.");
  await pending;
});
