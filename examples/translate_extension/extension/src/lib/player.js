// Reads a translation aloud segment by segment, for extension pages (the popup).
// src/content/content.js has its own copy of this loop, since content scripts can't import modules.

import { base64ToBuffer } from './base64.js';

/**
 * Starts reading `text` aloud. Call it inside a click handler so the browser allows sound.
 * onState(state, detail) receives "loading" | "playing" | "done" | "error" (detail = the error response).
 * Returns stop(), which ends playback at once.
 */
export function readAloud({ text, target, send, onState }) {
  const ctx = new AudioContext();
  let source = null;
  let stopped = false;

  const stop = () => {
    if (stopped) return;
    stopped = true;
    try {
      source?.stop();
    } catch {
      // Not started yet.
    }
    ctx.close();
  };

  (async () => {
    onState('loading');
    let next = send({ type: 'speak', text, target, index: 0 });
    for (let i = 0; ; i++) {
      const res = await next;
      if (stopped) return;
      if (!res.ok) {
        stop();
        return onState('error', res);
      }
      // Fetch the next segment while this one plays: each takes a few seconds to make.
      if (i + 1 < res.count) next = send({ type: 'speak', text, target, index: i + 1 });
      let buffer;
      try {
        buffer = await ctx.decodeAudioData(base64ToBuffer(res.audio));
      } catch {
        stop();
        return onState('error', { error: 'Could not play the audio from Bodhan.' });
      }
      if (stopped) return;
      onState('playing');
      await new Promise((resolve) => {
        source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);
        source.onended = resolve;
        source.start();
      });
      if (stopped) return;
      if (i + 1 >= res.count) break;
    }
    stop();
    onState('done');
  })();

  return stop;
}
