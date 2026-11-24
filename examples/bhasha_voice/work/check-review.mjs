// Tests the review lifecycle with mocked React hooks; no recording or API traffic.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../packages/bhashavoice/src/useTranscriptReview.js", import.meta.url), "utf8");

async function harness(text, onInsert) {
  const slots = [];
  let cursor = 0;
  let cleanup;
  let updates = 0;
  const context = vm.createContext({});
  const react = new vm.SyntheticModule(["useState", "useRef", "useEffect"], function () {
    this.setExport("useState", (initial) => {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value) => { slots[index] = value; updates++; }];
    });
    this.setExport("useRef", (initial) => {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    });
    this.setExport("useEffect", (effect) => { cleanup ??= effect(); });
  }, { context });
  const hook = new vm.SourceTextModule(source, { context });
  await hook.link(() => react);
  await hook.evaluate();
  return {
    render() { cursor = 0; return hook.namespace.default(text, onInsert); },
    unmount() { cleanup(); },
    updates: () => updates,
  };
}

test("review edits stay local until Insert returns the exact reviewed text", async () => {
  const calls = [];
  const original = "Original transcript.";
  const h = await harness(original, (text) => calls.push(text));
  assert.equal(h.render().text, original);
  h.render().edit("  Corrected words.\nA second line.  ");
  assert.deepEqual(calls, []);
  await h.render().insert();
  assert.deepEqual(calls, ["  Corrected words.\nA second line.  "]);
  assert.equal(h.render().status, "inserted");
  assert.equal(original, "Original transcript.");
});

test("Insert is locked immediately while the host callback is pending and after success", async () => {
  let resolve;
  let calls = 0;
  const h = await harness("Reviewed text.", () => { calls++; return new Promise((r) => { resolve = r; }); });
  const handler = h.render().insert;
  const pending = handler();
  await handler();
  assert.equal(calls, 1);
  assert.equal(h.render().status, "inserting");
  assert.equal(h.render().canInsert, false);
  h.render().edit("must be ignored while inserting");
  assert.equal(h.render().text, "Reviewed text.");
  resolve();
  await pending;
  await handler();
  assert.equal(calls, 1);
  assert.equal(h.render().canInsert, false);
});

test("empty or whitespace-only review cannot be inserted", async () => {
  let calls = 0;
  const h = await harness("Original", () => calls++);
  for (const text of ["", " \n\t "]) {
    h.render().edit(text);
    assert.equal(h.render().canInsert, false);
    await h.render().insert();
  }
  assert.equal(calls, 0);
});

test("without a host callback, editing works but insertion is disabled", async () => {
  const h = await harness("Original");
  h.render().edit("Edited");
  assert.equal(h.render().text, "Edited");
  assert.equal(h.render().canInsert, false);
  await h.render().insert();
  assert.equal(h.render().status, "review");
});

test("a throwing host callback preserves edits and permits retry", async () => {
  let fail = true;
  const calls = [];
  const h = await harness("Original", (text) => {
    if (fail) throw new Error("private host failure");
    calls.push(text);
  });
  h.render().edit("My reviewed text.");
  await h.render().insert();
  assert.equal(h.render().text, "My reviewed text.");
  assert.equal(h.render().status, "review");
  assert.equal(h.render().canInsert, true);
  assert.ok(!h.render().error.includes("private host"));
  fail = false;
  await h.render().insert();
  assert.deepEqual(calls, ["My reviewed text."]);
});

test("an asynchronous callback rejection does not claim insertion succeeded", async () => {
  const h = await harness("Reviewed text", async () => { throw new Error("failure"); });
  await h.render().insert();
  assert.equal(h.render().status, "review");
  assert.equal(h.render().text, "Reviewed text");
  assert.match(h.render().error, /edits are still here/);
  h.render().edit("Corrected again");
  assert.equal(h.render().error, "");
});

test("an unmounted review ignores the completion of a pending callback", async () => {
  let resolve;
  const h = await harness("Reviewed text", () => new Promise((r) => { resolve = r; }));
  const pending = h.render().insert();
  h.unmount();
  const updates = h.updates();
  resolve();
  await pending;
  assert.equal(h.updates(), updates);
});

test("Insert translation preserves the original and shares the single insertion lock", async () => {
  const calls = [];
  const h = await harness("Original source", (value) => calls.push(value));
  h.render().edit("Reviewed original");
  await h.render().insert("  Edited translation\nsecond line  ");
  assert.deepEqual(calls, ["  Edited translation\nsecond line  "]);
  assert.equal(h.render().text, "Reviewed original");
  await h.render().insert();
  assert.equal(calls.length, 1);
});

test("blank translations and event objects cannot reach the host callback", async () => {
  const h = await harness("Original", () => assert.fail("Unexpected Insert"));
  await h.render().insert(" \n ");
  await h.render().insert({ type: "click" });
  assert.equal(h.render().status, "review");
});
