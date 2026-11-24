// Isolated lifecycle checks. These mock React hooks and browser media APIs;
// they do not verify a physical microphone or React's renderer.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../packages/bhashavoice/src/useVoiceRecorder.js", import.meta.url),
  "utf8",
);

function media() {
  const track = { stopped: false, stop() { this.stopped = true; } };
  return { track, stream: { getTracks: () => [track] } };
}

class Recorder {
  constructor(stream) {
    this.stream = stream;
    this.state = "inactive";
    this.mimeType = "audio/webm;codecs=opus";
  }
  start() { this.state = "recording"; }
  stop() {
    this.state = "inactive";
    queueMicrotask(() => {
      this.ondataavailable?.({ data: new Blob(["test audio"], { type: this.mimeType }) });
      this.onstop?.();
    });
  }
}

function fakeClock() {
  let now = 0;
  let id = 0;
  const timers = new Map();
  return {
    now: () => now,
    setTimeout(fn, delay) {
      timers.set(++id, { fn, at: now + delay });
      return id;
    },
    setInterval(fn, delay) {
      timers.set(++id, { fn, at: now + delay, interval: delay });
      return id;
    },
    clear: (timer) => timers.delete(timer),
    count: () => timers.size,
    jump(ms) { now += ms; },
    advance(ms) {
      const target = now + ms;
      while (true) {
        const next = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
        if (!next || next[1].at > target) break;
        const [timerId, timer] = next;
        now = Math.max(now, timer.at);
        if (timer.interval) timer.at = now + timer.interval;
        else timers.delete(timerId);
        timer.fn();
      }
      now = target;
    },
  };
}

async function harness(getUserMedia, RecorderClass = Recorder, audioBrowser = {}) {
  const slots = [];
  let cursor = 0;
  let cleanup;
  const previews = [];
  const revoked = [];
  const clock = fakeClock();
  const context = vm.createContext({
    window: { isSecureContext: true, MediaRecorder: RecorderClass, ...audioBrowser },
    navigator: { mediaDevices: { getUserMedia } },
    MediaRecorder: RecorderClass,
    Blob,
    performance: { now: clock.now },
    setTimeout: clock.setTimeout,
    setInterval: clock.setInterval,
    clearTimeout: clock.clear,
    clearInterval: clock.clear,
    URL: {
      createObjectURL(blob) {
        const url = `blob:preview-${previews.length}`;
        previews.push({ blob, url });
        return url;
      },
      revokeObjectURL(url) { revoked.push(url); },
    },
  });
  const react = new vm.SyntheticModule(["useState", "useRef", "useEffect"], function () {
    this.setExport("useState", (initial) => {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value) => { slots[index] = value; }];
    });
    this.setExport("useRef", (initial) => {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    });
    this.setExport("useEffect", (effect) => { cleanup ??= effect(); });
  }, { context });
  const recorderModule = new vm.SourceTextModule(source, { context });
  const activityModule = new vm.SourceTextModule(await readFile(new URL("../packages/bhashavoice/src/microphoneActivity.mjs", import.meta.url), "utf8"), { context });
  await recorderModule.link(name => name === "react" ? react : activityModule);
  await recorderModule.evaluate();
  return {
    render() { cursor = 0; return recorderModule.namespace.default(); },
    unmount() { cleanup(); },
    previews,
    revoked,
    clock,
  };
}

test("requests audio only on start, then Stop releases the microphone", async () => {
  const { stream, track } = media();
  let calls = 0;
  const h = await harness(async (constraints) => {
    calls++;
    assert.equal(constraints.audio, true);
    assert.equal(constraints.video, undefined);
    return stream;
  });
  assert.equal(h.render().status, "idle");
  assert.equal(calls, 0);
  await h.render().start();
  assert.equal(h.render().status, "recording");
  h.render().stop();
  assert.equal(track.stopped, true);
  assert.equal(h.render().status, "stopping");
  await Promise.resolve();
  assert.equal(h.render().status, "stopped");
  assert.equal(h.render().audioUrl, "blob:preview-0");
  assert.equal(h.previews[0].blob.type, "audio/webm;codecs=opus");
  assert.equal(await h.previews[0].blob.text(), "test audio");
  assert.equal(h.render().audioBlob, h.previews[0].blob);
});

test("repeated clicks do not open multiple permission requests", async () => {
  const { stream, track } = media();
  let resolve;
  let calls = 0;
  const h = await harness(() => { calls++; return new Promise((r) => { resolve = r; }); });
  const pending = h.render().start();
  await h.render().start();
  assert.equal(calls, 1);
  assert.equal(h.render().status, "requesting");
  resolve(stream);
  await pending;
  h.unmount();
  assert.equal(track.stopped, true);
});

test("unmount releases an active microphone", async () => {
  const { stream, track } = media();
  const h = await harness(async () => stream);
  await h.render().start();
  h.unmount();
  assert.equal(track.stopped, true);
});

test("permission arriving after unmount immediately releases its stream", async () => {
  const { stream, track } = media();
  let resolve;
  let recorders = 0;
  class CountingRecorder extends Recorder { constructor(s) { super(s); recorders++; } }
  const h = await harness(() => new Promise((r) => { resolve = r; }), CountingRecorder);
  const pending = h.render().start();
  h.unmount();
  resolve(stream);
  await pending;
  assert.equal(track.stopped, true);
  assert.equal(recorders, 0);
});

test("permission denial shows an error and allows retry", async () => {
  const { stream, track } = media();
  let denied = true;
  const h = await harness(async () => {
    if (denied) throw { name: "NotAllowedError" };
    return stream;
  });
  await h.render().start();
  assert.equal(h.render().status, "error");
  assert.match(h.render().error, /blocked/);
  denied = false;
  await h.render().start();
  assert.equal(h.render().error, "");
  assert.equal(h.render().status, "recording");
  h.render().stop();
  assert.equal(track.stopped, true);
});

test("recorder setup failure releases the acquired microphone", async () => {
  const { stream, track } = media();
  class BrokenRecorder { constructor() { throw new Error("setup failed"); } }
  const h = await harness(async () => stream, BrokenRecorder);
  await h.render().start();
  assert.equal(track.stopped, true);
  assert.equal(h.render().status, "error");
});

test("recorder errors release the microphone and show an error", async () => {
  const { stream, track } = media();
  let recorder;
  class CapturedRecorder extends Recorder { constructor(s) { super(s); recorder = this; } }
  const h = await harness(async () => stream, CapturedRecorder);
  await h.render().start();
  recorder.onerror();
  assert.equal(track.stopped, true);
  assert.equal(h.render().status, "error");
  assert.match(h.render().error, /interrupted/);
});

test("a recorder stop failure still releases the microphone", async () => {
  const { stream, track } = media();
  class BrokenStopRecorder extends Recorder { stop() { throw new Error("device lost"); } }
  const h = await harness(async () => stream, BrokenStopRecorder);
  await h.render().start();
  h.render().stop();
  assert.equal(track.stopped, true);
  assert.equal(h.render().status, "error");
});

test("unsupported recorder does not request microphone permission", async () => {
  let calls = 0;
  const h = await harness(async () => { calls++; }, null);
  await h.render().start();
  assert.equal(calls, 0);
  assert.match(h.render().error, /does not support/);
});

test("Discard revokes the preview and permits a fresh recording", async () => {
  let calls = 0;
  const h = await harness(async () => { calls++; return media().stream; });
  await h.render().start();
  h.render().stop();
  await Promise.resolve();
  await h.render().start();
  assert.equal(calls, 1, "preview must be discarded before starting again");
  h.render().discard();
  assert.deepEqual(h.revoked, ["blob:preview-0"]);
  assert.equal(h.render().audioUrl, "");
  assert.equal(h.render().audioBlob, null);
  assert.equal(h.render().status, "idle");
  await h.render().start();
  assert.equal(calls, 2);
  h.render().stop();
  await Promise.resolve();
  assert.equal(h.render().audioUrl, "blob:preview-1");
  h.unmount();
  assert.deepEqual(h.revoked, ["blob:preview-0", "blob:preview-1"]);
});

test("unmount while finishing cannot create a stale preview", async () => {
  const h = await harness(async () => media().stream);
  await h.render().start();
  h.render().stop();
  h.unmount();
  await Promise.resolve();
  assert.equal(h.previews.length, 0);
});

test("a recorder producing no audio shows an error without a preview", async () => {
  class EmptyRecorder extends Recorder {
    stop() {
      this.state = "inactive";
      queueMicrotask(() => {
        this.ondataavailable?.({ data: new Blob([]) });
        this.onstop?.();
      });
    }
  }
  const { stream, track } = media();
  const h = await harness(async () => stream, EmptyRecorder);
  await h.render().start();
  h.render().stop();
  await Promise.resolve();
  assert.equal(track.stopped, true);
  assert.equal(h.render().status, "error");
  assert.equal(h.previews.length, 0);
});

test("final chunk is kept and repeated Stop calls do not duplicate previews", async () => {
  let recorder;
  class CapturedRecorder extends Recorder { constructor(s) { super(s); recorder = this; } }
  const h = await harness(async () => media().stream, CapturedRecorder);
  await h.render().start();
  recorder.ondataavailable({ data: new Blob(["first-"]) });
  h.render().stop();
  h.render().stop();
  await Promise.resolve();
  assert.equal(h.previews.length, 1);
  assert.equal(await h.previews[0].blob.text(), "first-test audio");
  h.unmount();
});

test("timer excludes permission waiting and measures elapsed recording time", async () => {
  let resolve;
  const h = await harness(() => new Promise((r) => { resolve = r; }));
  const pending = h.render().start();
  h.clock.advance(10000);
  assert.equal(h.render().elapsedSeconds, 0);
  assert.equal(h.clock.count(), 0);
  resolve(media().stream);
  await pending;
  h.clock.advance(1200);
  assert.equal(h.render().elapsedSeconds, 1);
  h.unmount();
});

test("the 25-second limit releases the microphone and retains playback", async () => {
  const { stream, track } = media();
  const h = await harness(async () => stream);
  await h.render().start();
  h.clock.advance(24999);
  assert.equal(track.stopped, false);
  assert.equal(h.render().status, "recording");
  h.clock.advance(1);
  assert.equal(track.stopped, true);
  assert.equal(h.render().elapsedSeconds, 25);
  assert.equal(h.render().reachedLimit, true);
  assert.equal(h.clock.count(), 0);
  await Promise.resolve();
  assert.equal(h.render().status, "stopped");
  assert.equal(h.previews.length, 1);
  h.render().discard();
  assert.equal(h.render().elapsedSeconds, 0);
  assert.equal(h.render().reachedLimit, false);
  await h.render().start();
  assert.equal(h.render().elapsedSeconds, 0);
  assert.equal(h.render().status, "recording");
  h.unmount();
});

test("manual Stop freezes elapsed time and cancels the automatic stop", async () => {
  const h = await harness(async () => media().stream);
  await h.render().start();
  h.clock.advance(3150);
  h.render().stop();
  assert.equal(h.clock.count(), 0);
  assert.equal(h.render().elapsedSeconds, 3);
  await Promise.resolve();
  h.clock.advance(50000);
  assert.equal(h.render().reachedLimit, false);
  assert.equal(h.render().elapsedSeconds, 3);
  assert.equal(h.previews.length, 1);
  h.unmount();
});

test("delayed timer callbacks use elapsed time rather than counting ticks", async () => {
  const h = await harness(async () => media().stream);
  await h.render().start();
  h.clock.jump(10000);
  h.clock.advance(0);
  assert.equal(h.render().elapsedSeconds, 10);
  h.clock.jump(20000);
  h.clock.advance(0);
  assert.equal(h.render().elapsedSeconds, 25);
  assert.equal(h.render().reachedLimit, true);
  await Promise.resolve();
  assert.equal(h.previews.length, 1);
  h.unmount();
});

test("Discard and unmount clear active recording timers", async () => {
  const h = await harness(async () => media().stream);
  await h.render().start();
  h.clock.advance(2000);
  h.render().discard();
  assert.equal(h.clock.count(), 0);
  assert.equal(h.render().elapsedSeconds, 0);
  h.clock.advance(30000);
  await Promise.resolve();
  assert.equal(h.previews.length, 0);
  await h.render().start();
  assert.equal(h.clock.count(), 2);
  h.unmount();
  assert.equal(h.clock.count(), 0);
});

test("recorder failures clear timers and cannot later trigger a limit stop", async () => {
  let recorder;
  class CapturedRecorder extends Recorder { constructor(s) { super(s); recorder = this; } }
  const h = await harness(async () => media().stream, CapturedRecorder);
  await h.render().start();
  h.clock.advance(2000);
  recorder.onerror();
  assert.equal(h.clock.count(), 0);
  h.clock.advance(30000);
  await Promise.resolve();
  assert.equal(h.render().status, "error");
  assert.equal(h.render().reachedLimit, false);
  assert.equal(h.previews.length, 0);
});

test("Cancel stops the microphone, resets the timer, and discards audio", async () => {
  const { stream, track } = media();
  const h = await harness(async () => stream);
  await h.render().start();
  h.clock.advance(3400);
  h.render().cancel();
  assert.equal(track.stopped, true);
  assert.equal(h.clock.count(), 0);
  assert.equal(h.render().status, "idle");
  assert.equal(h.render().elapsedSeconds, 0);
  assert.equal(h.render().reachedLimit, false);
  assert.equal(h.render().audioUrl, "");
  h.clock.advance(30000);
  await Promise.resolve();
  assert.equal(h.previews.length, 0);
});

test("Cancel during permission waiting stops a late stream without starting a recorder", async () => {
  const { stream, track } = media();
  let resolve;
  let recorders = 0;
  class CountingRecorder extends Recorder { constructor(s) { super(s); recorders++; } }
  const h = await harness(() => new Promise((r) => { resolve = r; }), CountingRecorder);
  const pending = h.render().start();
  h.render().cancel();
  assert.equal(h.render().status, "idle");
  resolve(stream);
  await pending;
  assert.equal(track.stopped, true);
  assert.equal(recorders, 0);
  assert.equal(h.clock.count(), 0);
  assert.equal(h.render().status, "idle");
  assert.equal(h.previews.length, 0);
});

test("a late permission rejection after Cancel cannot replace the idle state", async () => {
  let reject;
  const h = await harness(() => new Promise((_, r) => { reject = r; }));
  const pending = h.render().start();
  h.render().cancel();
  reject({ name: "NotAllowedError" });
  await pending;
  assert.equal(h.render().status, "idle");
  assert.equal(h.render().error, "");
});

test("a cancelled permission response cannot interfere with a newer recording", async () => {
  const oldMedia = media();
  const newMedia = media();
  let resolveOld;
  let calls = 0;
  const h = await harness(() => {
    calls++;
    return calls === 1 ? new Promise((r) => { resolveOld = r; }) : Promise.resolve(newMedia.stream);
  });
  const oldPending = h.render().start();
  h.render().cancel();
  await h.render().start();
  resolveOld(oldMedia.stream);
  await oldPending;
  assert.equal(oldMedia.track.stopped, true);
  assert.equal(newMedia.track.stopped, false);
  assert.equal(h.render().status, "recording");
  h.clock.advance(1000);
  assert.equal(h.render().elapsedSeconds, 1);
  h.render().stop();
  await Promise.resolve();
  assert.equal(h.previews.length, 1);
  h.unmount();
});

test("Cancel during finalization prevents a queued preview and permits retry", async () => {
  const h = await harness(async () => media().stream);
  await h.render().start();
  h.render().stop();
  h.render().cancel();
  await Promise.resolve();
  assert.equal(h.previews.length, 0);
  assert.equal(h.render().status, "idle");
  await h.render().start();
  assert.equal(h.render().status, "recording");
  h.render().cancel();
  h.render().cancel();
  await Promise.resolve();
  assert.equal(h.render().status, "idle");
  assert.equal(h.previews.length, 0);
});


test("meter shares the recording stream and closes immediately on Stop, Cancel, limit, error or unmount", async () => {
  for (const exit of ["stop", "cancel", "limit", "error", "unmount"]) {
    const { stream, track } = media();
    let recorder, context, frameCallback, cancelled = 0;
    class CapturedRecorder extends Recorder { constructor(s) { super(s); recorder = this; } }
    class AudioContext {
      state = "running";
      closed = false;
      constructor() { context = this; }
      createMediaStreamSource(value) {
        assert.equal(value, stream);
        return { connect() {}, disconnect() {} };
      }
      createAnalyser() {
        return { fftSize: 1024, getFloatTimeDomainData(data) { for (let i=0;i<data.length;i++) data[i] = i%2 ? 0.1 : -0.1; }, disconnect() {} };
      }
      close() { this.closed = true; return Promise.resolve(); }
    }
    const h = await harness(async () => stream, CapturedRecorder, {
      AudioContext,
      requestAnimationFrame(fn) { frameCallback = fn; return 1; },
      cancelAnimationFrame() { cancelled++; },
    });
    await h.render().start();
    frameCallback(0);
    assert.equal(h.render().audioActivity.available, true);
    assert.ok(h.render().audioActivity.levels[3] > 0);
    if (exit === "limit") h.clock.advance(25000);
    else if (exit === "error") recorder.onerror();
    else if (exit === "unmount") h.unmount();
    else h.render()[exit]();
    assert.equal(context.closed, true, exit);
    assert.equal(track.stopped, true, exit);
    assert.equal(cancelled, 1, exit);
    if (exit !== "unmount") assert.equal(h.render().audioActivity.available, false);
    await Promise.resolve();
  }
});
