import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);
export const MAX_AUDIO_BYTES = 5 * 1024 * 1024;
export const MAX_AUDIO_SECONDS = 25;
const SAMPLE_RATE = 16000;
const BYTES_PER_SECOND = SAMPLE_RATE * 2;

export class AudioError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

function wavDuration(wav) {
  const invalid = () => new AudioError("INVALID_AUDIO", "The file could not be decoded as audio.", 415);
  if (wav.length < 44 || wav.toString("ascii", 0, 4) !== "RIFF" || wav.toString("ascii", 8, 12) !== "WAVE") {
    throw invalid();
  }
  let validFormat = false;
  let audioBytes = 0;
  for (let offset = 12; offset + 8 <= wav.length;) {
    const type = wav.toString("ascii", offset, offset + 4);
    const size = wav.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (start + size > wav.length) throw invalid();
    if (type === "fmt ") {
      if (size < 16) throw invalid();
      validFormat = wav.readUInt16LE(start) === 1
        && wav.readUInt16LE(start + 2) === 1
        && wav.readUInt32LE(start + 4) === SAMPLE_RATE
        && wav.readUInt16LE(start + 14) === 16;
    }
    if (type === "data") audioBytes += size;
    offset = start + size + (size % 2);
  }
  if (!validFormat || !audioBytes || audioBytes % 2) throw invalid();
  return audioBytes / BYTES_PER_SECOND;
}

export async function prepareAudio(file, {
  signal,
  tempRoot = tmpdir(),
  ffmpegPath = process.env.FFMPEG_PATH || "ffmpeg",
} = {}) {
  if (!file.size) throw new AudioError("EMPTY_AUDIO", "The recording is empty.", 400);
  if (file.size > MAX_AUDIO_BYTES) {
    throw new AudioError("AUDIO_TOO_LARGE", "Audio must be 5 MiB or smaller.", 413);
  }

  const directory = await mkdtemp(join(tempRoot, "bashavoice-"));
  try {
    signal?.throwIfAborted();
    const input = join(directory, "input.bin");
    const output = join(directory, "output.wav");
    await writeFile(input, Buffer.from(await file.arrayBuffer()), { mode: 0o600, signal });
    try {
      await run(ffmpegPath, [
        "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-threads", "1", "-filter_threads", "1",
        "-protocol_whitelist", "file,pipe",
        "-format_whitelist", "wav,mp3,ogg,flac,matroska,webm,mov,mp4,m4a,3gp,3g2,mj2,aac",
        "-i", input, "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1",
        // Decode one extra second to reject long input instead of silently trimming it.
        "-t", String(MAX_AUDIO_SECONDS + 1), "-ac", "1", "-ar", String(SAMPLE_RATE),
        "-c:a", "pcm_s16le", "-threads", "1", "-fs", "900000", "-f", "wav", output,
      ], { timeout: 20000, killSignal: "SIGKILL", maxBuffer: 64 * 1024, signal });
    } catch (error) {
      if (error.code === "ENOENT") {
        throw new AudioError("CONVERTER_UNAVAILABLE", "Audio conversion is not configured on this server.", 503);
      }
      if (signal?.aborted || error.killed || error.code === "ABORT_ERR") {
        throw new AudioError("PROCESSING_TIMEOUT", "Audio processing was cancelled or timed out. Please try again.", 408);
      }
      throw new AudioError("INVALID_AUDIO", "The file could not be decoded as supported audio.", 415);
    }
    const wav = await readFile(output, { signal });
    const durationSeconds = wavDuration(wav);
    if (durationSeconds > MAX_AUDIO_SECONDS) {
      throw new AudioError("AUDIO_TOO_LONG", "Recordings must be 25 seconds or shorter.", 422);
    }
    return { wav, durationSeconds, sampleRate: SAMPLE_RATE, channels: 1 };
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}
