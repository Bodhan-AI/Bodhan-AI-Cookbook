import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import { handleAudioUpload as handleUpload } from "../src/server/audio/handleAudioUpload.mjs";
import { MAX_AUDIO_BYTES, prepareAudio } from "../src/server/audio/prepareAudio.mjs";

const run = promisify(execFile);
// Real decoding, but no provider traffic or API credit in automated tests.
const handleAudioUpload = (request) => handleUpload(request, {
  transcribe: async () => "Test transcript.",
});

function sineWav(seconds = 1) {
  const samples = seconds * 16000;
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write("RIFF", 0);
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24);
  wav.writeUInt32LE(32000, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(4000 * Math.sin(2 * Math.PI * 440 * i / 16000)), 44 + i * 2);
  return wav;
}

function upload(file, language = "en", options = {}) {
  const form = new FormData();
  if (file !== undefined) form.set("file", file);
  if (language !== undefined) form.set("language", language);
  return new Request("http://localhost/api/bhasha/transcribe", { method: "POST", body: form, ...options });
}

async function expectError(request, status, code) {
  const response = await handleAudioUpload(request);
  assert.equal(response.status, status);
  assert.equal((await response.json()).error.code, code);
}

test("WAV upload returns metadata and the provider transcript", async () => {
  const response = await handleAudioUpload(upload(new File([sineWav()], "recording.wav", { type: "audio/wav" })));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  const result = await response.json();
  assert.equal(result.status, "transcribed");
  assert.equal(result.language, "en");
  assert.equal(result.text, "Test transcript.");
  assert.equal(result.audio.durationSeconds, 1);
  assert.equal(result.audio.sampleRate, 16000);
  assert.equal(result.audio.channels, 1);
  assert.equal(result.audio.mimeType, "audio/wav");
});

test("browser-style WebM/Opus and M4A/AAC are decoded to WAV", async () => {
  for (const [format, type, codec, flags] of [
    ["webm", "audio/webm;codecs=opus", "libopus", []],
    ["mp4", "audio/mp4", "aac", ["-movflags", "frag_keyframe+empty_moov"]],
  ]) {
    const { stdout } = await run("ffmpeg", [
      "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
      "-c:a", codec, ...flags, "-f", format, "pipe:1",
    ], { encoding: "buffer", maxBuffer: 1024 * 1024 });
    const response = await handleAudioUpload(upload(new File([stdout], `recording.${format}`, { type }), "hi"));
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.ok(result.audio.durationSeconds >= 0.9 && result.audio.durationSeconds <= 1.2);
    assert.equal(result.audio.mimeType, "audio/wav");
  }
});

test("25 seconds is accepted and a longer recording is rejected rather than trimmed", async () => {
  const valid = await handleAudioUpload(upload(new File([sineWav(25)], "cap.wav", { type: "audio/wav" })));
  assert.equal(valid.status, 200);
  assert.equal((await valid.json()).audio.durationSeconds, 25);
  await expectError(upload(new File([sineWav(27)], "long.wav", { type: "audio/wav" })), 422, "AUDIO_TOO_LONG");
});

test("missing, text-only, and empty file fields are rejected", async () => {
  await expectError(upload(), 400, "MISSING_AUDIO");
  await expectError(upload("not a file"), 400, "MISSING_AUDIO");
  await expectError(upload(new File([], "empty.wav", { type: "audio/wav" })), 400, "EMPTY_AUDIO");
});

test("invalid language and unsupported MIME types are rejected", async () => {
  const file = new File([sineWav()], "audio.wav", { type: "audio/wav" });
  await expectError(upload(file, "fr"), 400, "INVALID_LANGUAGE");
  await expectError(upload(new File(["hello"], "text.txt", { type: "text/plain" })), 415, "UNSUPPORTED_AUDIO_TYPE");
});

test("mislabeled corrupt audio is rejected by the decoder", async () => {
  await expectError(upload(new File(["not actually audio"], "fake.wav", { type: "audio/wav" })), 415, "INVALID_AUDIO");
});

test("file size is enforced even when Content-Length is absent", async () => {
  await expectError(upload(new File([new Uint8Array(MAX_AUDIO_BYTES + 1)], "large.wav", { type: "audio/wav" })), 413, "AUDIO_TOO_LARGE");
});

test("streamed request bodies cannot exceed the body limit", async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(MAX_AUDIO_BYTES + 64 * 1024 + 1));
      controller.close();
    },
  });
  await expectError(new Request("http://localhost/api/bhasha/transcribe", {
    method: "POST", headers: { "content-type": "multipart/form-data; boundary=test" }, body, duplex: "half",
  }), 413, "UPLOAD_TOO_LARGE");
});

test("non-multipart and malformed multipart requests get safe errors", async () => {
  await expectError(new Request("http://localhost/api/bhasha/transcribe", { method: "POST", body: "text" }), 415, "INVALID_CONTENT_TYPE");
  await expectError(new Request("http://localhost/api/bhasha/transcribe", {
    method: "POST", headers: { "content-type": "multipart/form-data; boundary=missing" }, body: "invalid",
  }), 400, "INVALID_FORM_DATA");
});

test("temporary conversion files are removed after success and failure", async () => {
  const root = await mkdtemp(join(tmpdir(), "bashavoice-test-"));
  try {
    await prepareAudio(new File([sineWav()], "audio.wav"), { tempRoot: root });
    assert.deepEqual(await readdir(root), []);
    await assert.rejects(prepareAudio(new File(["broken"], "bad.wav"), { tempRoot: root }), { code: "INVALID_AUDIO" });
    assert.deepEqual(await readdir(root), []);
    await assert.rejects(prepareAudio(new File([sineWav()], "audio.wav"), {
      tempRoot: root, ffmpegPath: "/nonexistent/bashavoice-ffmpeg",
    }), { code: "CONVERTER_UNAVAILABLE" });
    assert.deepEqual(await readdir(root), []);
    const controller = new AbortController();
    controller.abort();
    await assert.rejects(prepareAudio(new File([sineWav()], "audio.wav"), { tempRoot: root, signal: controller.signal }));
    assert.deepEqual(await readdir(root), []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("cancelled requests return a safe cancellation error", async () => {
  const controller = new AbortController();
  controller.abort();
  await expectError(upload(new File([sineWav()], "audio.wav", { type: "audio/wav" }), "en", { signal: controller.signal }), 408, "PROCESSING_TIMEOUT");
});

test("a third simultaneous upload is rejected while two uploads are pending", async () => {
  const controllers = [new AbortController(), new AbortController()];
  const pending = controllers.map((controller) => handleAudioUpload(new Request("http://localhost/api/bhasha/transcribe", {
    method: "POST", headers: { "content-type": "multipart/form-data; boundary=test" },
    body: new ReadableStream(), duplex: "half", signal: controller.signal,
  })));
  try {
    await expectError(upload(new File([sineWav()], "audio.wav", { type: "audio/wav" })), 429, "SERVER_BUSY");
  } finally {
    controllers.forEach((controller) => controller.abort());
    const results = await Promise.all(pending);
    results.forEach((result) => assert.equal(result.status, 408));
  }
});
