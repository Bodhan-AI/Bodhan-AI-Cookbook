import test from "node:test";
import assert from "node:assert/strict";
import { microphoneLevel, startMicrophoneActivity } from "../packages/bhashavoice/src/microphoneActivity.mjs";

function tone(amplitude) {
  return Float32Array.from({length:1024}, (_, i) => amplitude * Math.sin(i * Math.PI / 16));
}

function browser({ failure, suspended = false } = {}) {
  const frames = new Map();
  const stats = { sources:0, connections:[], disconnected:0, closed:0, reads:0, resumed:0 };
  let next = 0, amplitude = 0, context;
  class AudioContext {
    state = suspended ? "suspended" : "running";
    constructor() { context = this; }
    createMediaStreamSource(stream) {
      if (failure === "source") throw new Error("Monitor unavailable");
      stats.sources++;
      stats.stream = stream;
      return { connect(node) { stats.connections.push(node); }, disconnect() { stats.disconnected++; } };
    }
    createAnalyser() {
      return { fftSize:1024, getFloatTimeDomainData(data) {
        if (failure === "read") throw new Error("Analysis failed");
        stats.reads++;
        data.set(tone(amplitude));
      }, disconnect() { stats.disconnected++; } };
    }
    resume() { stats.resumed++; return failure === "resume" ? Promise.reject(new Error("Blocked")) : Promise.resolve().then(()=>{this.state="running";}); }
    close() { stats.closed++; this.state="closed"; return Promise.resolve(); }
  }
  return {
    api: { AudioContext, requestAnimationFrame(fn) { frames.set(++next, fn); return next; }, cancelAnimationFrame(id) { frames.delete(id); } },
    stats, frames,
    signal(value) { amplitude = value; },
    suspend() { context.state = "suspended"; },
    frame(time) { const queue=[...frames.values()]; frames.clear(); queue.forEach(fn=>fn(time)); },
  };
}

test("silence, DC offset and low noise remain flat; louder speech produces higher bounded levels", () => {
  assert.equal(microphoneLevel(new Float32Array(1024)), 0);
  assert.equal(microphoneLevel(new Float32Array(1024).fill(0.25)), 0);
  assert.equal(microphoneLevel(tone(0.0005)), 0);
  assert.ok(microphoneLevel(tone(0.1)) > microphoneLevel(tone(0.01)));
  assert.equal(microphoneLevel(tone(1)), 1);
});

test("bars contain recent measured levels and settle to zero during silence", () => {
  const b = browser(), updates = [], stream = {};
  const stop = startMicrophoneActivity(stream, value=>updates.push(value), b.api);
  b.frame(0);
  assert.deepEqual(updates.at(-1).levels, [0,0,0,0]);
  b.signal(0.1);
  b.frame(80);
  assert.ok(updates.at(-1).levels[3] > 0);
  b.signal(0);
  for (let t=160;t<=400;t+=80) b.frame(t);
  assert.deepEqual(updates.at(-1).levels, [0,0,0,0]);
  assert.equal(b.stats.stream, stream);
  assert.equal(b.stats.sources, 1);
  assert.equal(b.stats.connections.length, 1, "only connect source to analyser, never speakers");
  stop();
});

test("sampling is throttled and cleanup cancels frames, disconnects nodes and closes once", () => {
  const b=browser(), updates=[];
  const stop=startMicrophoneActivity({},value=>updates.push(value),b.api);
  b.frame(0); b.frame(16); b.frame(32); b.frame(80);
  assert.equal(updates.length,2);
  stop(); stop(); b.frame(160);
  assert.equal(updates.length,2);
  assert.equal(b.frames.size,0);
  assert.equal(b.stats.disconnected,2);
  assert.equal(b.stats.closed,1);
});

test("missing Web Audio, setup failure and read failure report unavailable without throwing", () => {
  const unavailable=[];
  startMicrophoneActivity({},value=>unavailable.push(value),{});
  assert.equal(unavailable[0].available,false);
  for (const failure of ["source","read"]) {
    const b=browser({failure}), updates=[];
    const stop=startMicrophoneActivity({},value=>updates.push(value),b.api);
    b.frame(0);
    assert.equal(updates.at(-1).available,false);
    assert.deepEqual(updates.at(-1).levels,[0,0,0,0]);
    assert.equal(b.stats.closed,1);
    assert.equal(b.frames.size,0);
    stop();
  }
});

test("suspended contexts resume; a blocked resume falls back and cannot update after cleanup", async () => {
  const b=browser({suspended:true}), updates=[];
  const stop=startMicrophoneActivity({},value=>updates.push(value),b.api);
  await Promise.resolve();
  b.frame(0);
  assert.equal(b.stats.resumed,1);
  assert.equal(updates.at(-1).available,true);
  b.suspend(); b.frame(80);
  assert.equal(updates.at(-1).available,false);
  assert.deepEqual(updates.at(-1).levels,[0,0,0,0]);
  stop();
  const blocked=browser({suspended:true,failure:"resume"}), errors=[];
  startMicrophoneActivity({},value=>errors.push(value),blocked.api);
  await Promise.resolve();
  assert.equal(errors.at(-1).available,false);
  assert.equal(blocked.stats.closed,1);
  const cancelled=browser({suspended:true,failure:"resume"});
  startMicrophoneActivity({},()=>assert.fail("late update"),cancelled.api)();
  await Promise.resolve();
});
