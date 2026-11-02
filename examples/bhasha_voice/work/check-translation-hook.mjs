import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
const source = await readFile(new URL("../packages/bhashavoice/src/useTranslation.js", import.meta.url), "utf8");
// Hook lifecycle simulation, with explicit rerenders and effect cleanup.
async function harness(send) {
  const slots = [];
  const effects = new Map();
  const timers = new Map();
  let cursor = 0;
  let args = ["Reviewed source", "hi", "/api/bhasha"];
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
  const client = new vm.SyntheticModule(["requestTranslation"], function () { this.setExport("requestTranslation", send); }, { context });
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

test("translation sends only on click and locks concurrent or duplicate successful requests", async () => {
  let calls = 0;
  let resolve;
  const h = await harness((text, target) => {
    assert.equal(text, "Reviewed source");
    assert.equal(target, "hi");
    calls++;
    return new Promise((r) => { resolve = r; });
  });
  const initial = h.render();
  assert.equal(calls, 0);
  const pending = initial.translate();
  await initial.translate();
  assert.equal(calls, 1);
  assert.equal(h.render().status, "loading");
  resolve("Translated text");
  await pending;
  h.render().edit("  Reviewed translation\nsecond line  ");
  assert.equal(h.render().text, "  Reviewed translation\nsecond line  ");
  await initial.translate();
  assert.equal(calls, 1);
  h.unmount();
});

test("a keyed panel remount for original edits, target changes or discard aborts old work", async () => {
  let signal;
  let resolve;
  const old = await harness((_, __, options) => {
    signal = options.signal;
    return new Promise((r) => { resolve = r; });
  });
  const pending = old.render().translate();
  old.unmount();
  assert.equal(signal.aborted, true);
  const fresh = await harness(async () => "Fresh translation");
  assert.equal(fresh.render().text, "");
  resolve("Stale translation");
  await pending;
  assert.equal(fresh.render().text, "");
  await fresh.render().translate();
  assert.equal(fresh.render().text, "Fresh translation");
  fresh.unmount();
});

test("backoff blocks repeated clicks without automatically retrying", async () => {
  let calls = 0;
  const h = await harness(async () => {
    if (++calls === 1) {
      const error = new Error("Translation busy"); error.retryAfterSeconds = 5; throw error;
    }
    return "Translation recovered";
  });
  const initial = h.render();
  await initial.translate();
  assert.equal(h.render().retryAfterSeconds, 5);
  await initial.translate();
  assert.equal(calls, 1);
  h.finishBackoff();
  assert.equal(calls, 1);
  await h.render().translate();
  assert.equal(calls, 2);
  h.unmount();
});

test("failed translation permits explicit retry and retains source text", async () => {
  let calls = 0;
  const h = await harness(async (source) => {
    assert.equal(source, "Reviewed source");
    if (++calls === 1) throw new Error("Translation failed");
    return "Recovered";
  });
  await h.render().translate();
  assert.equal(h.render().status, "error");
  await h.render().translate();
  assert.equal(h.render().text, "Recovered");
  h.unmount();
});

test("blank and oversized sources never submit translation requests", async () => {
  const h = await harness(() => assert.fail("Unexpected request"));
  await h.render(" ", "hi", "/api/bhasha").translate();
  await h.render("a".repeat(2001), "hi", "/api/bhasha").translate();
  assert.equal(h.render().status, "idle");
  h.unmount();
});

