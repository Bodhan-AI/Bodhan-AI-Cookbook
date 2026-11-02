// RMS measures actual waveform energy. Ignore DC offset and map a fixed dB
// range to the meter; silence must not be amplified by automatic normalization.
export function microphoneLevel(samples) {
  if (!samples.length) return 0;
  let mean = 0;
  for (const sample of samples) mean += sample;
  mean /= samples.length;
  let energy = 0;
  for (const sample of samples) energy += (sample - mean) ** 2;
  const rms = Math.sqrt(energy / samples.length);
  if (rms <= 0.001) return 0;
  return Math.max(0, Math.min(1, (20 * Math.log10(rms) + 60) / 48));
}

// Observe the recorder's existing stream. No extra microphone request and no
// connection to the speakers. Meter failure must never prevent recording.
export function startMicrophoneActivity(stream, update, browser = window) {
  let context, source, analyser, frame;
  let closed = false;
  let levels = [0, 0, 0, 0];
  let previous = -Infinity;
  function stop() {
    if (closed) return;
    closed = true;
    if (frame != null) browser.cancelAnimationFrame(frame);
    try { source?.disconnect(); } catch { /* Already disconnected. */ }
    try { analyser?.disconnect(); } catch { /* Already disconnected. */ }
    try { void context?.close()?.catch(() => {}); } catch { /* Already closed. */ }
  }
  function unavailable() {
    if (closed) return;
    stop();
    update({ available: false, levels: [0, 0, 0, 0] });
  }
  try {
    const AudioContext = browser.AudioContext || browser.webkitAudioContext;
    if (!AudioContext || !browser.requestAnimationFrame) {
      unavailable();
      return stop;
    }
    context = new AudioContext();
    source = context.createMediaStreamSource(stream);
    analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    function sample(time) {
      if (closed) return;
      try {
        if (time - previous >= 80) {
          previous = time;
          const available = context.state === "running";
          if (available) analyser.getFloatTimeDomainData(samples);
          const level = available ? microphoneLevel(samples) : 0;
          levels = available ? [...levels.slice(1), level] : [0, 0, 0, 0];
          update({ available, levels });
        }
        frame = browser.requestAnimationFrame(sample);
      } catch { unavailable(); }
    }
    if (context.state === "suspended") void context.resume().catch(unavailable);
    frame = browser.requestAnimationFrame(sample);
  } catch { unavailable(); }
  return stop;
}
