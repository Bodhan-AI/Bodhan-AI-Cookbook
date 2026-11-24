/**
 * AudioRecorder provides browser microphone recording directly to 16-bit PCM WAV (16,000 Hz, Mono).
 * This satisfies Bodhan AI Indic-Transcribe's strict WAV audio input requirements.
 */

export interface RecordingResult {
  blob: Blob;
  url: string;
  durationSeconds: number;
}

export class WavAudioRecorder {
  private audioContext: AudioContext | null = null;
  private mediaStream: MediaStream | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private audioData: Float32Array[] = [];
  private recordingLength = 0;
  private sampleRate = 16000;
  private startTime = 0;
  private timerInterval: any = null;

  async start(onTimeUpdate?: (seconds: number) => void): Promise<void> {
    this.audioData = [];
    this.recordingLength = 0;

    this.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: this.sampleRate,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });

    const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
    this.audioContext = new AudioCtx({ sampleRate: this.sampleRate });

    this.source = this.audioContext.createMediaStreamSource(this.mediaStream);
    // Buffer size 4096, 1 input channel, 1 output channel
    this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);

    this.processor.onaudioprocess = (e) => {
      const channelData = e.inputBuffer.getChannelData(0);
      this.audioData.push(new Float32Array(channelData));
      this.recordingLength += channelData.length;
    };

    this.source.connect(this.processor);
    this.processor.connect(this.audioContext.destination);

    this.startTime = Date.now();
    if (onTimeUpdate) {
      this.timerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
        onTimeUpdate(elapsed);
      }, 500);
    }
  }

  stop(): Promise<RecordingResult> {
    return new Promise((resolve, reject) => {
      try {
        if (this.timerInterval) {
          clearInterval(this.timerInterval);
          this.timerInterval = null;
        }

        const durationSeconds = Math.max(1, Math.round((Date.now() - this.startTime) / 1000));

        if (this.processor && this.source) {
          this.source.disconnect();
          this.processor.disconnect();
        }

        if (this.mediaStream) {
          this.mediaStream.getTracks().forEach((track) => track.stop());
        }

        if (this.audioContext && this.audioContext.state !== 'closed') {
          this.audioContext.close();
        }

        // Flatten all chunks
        const mergedSamples = new Float32Array(this.recordingLength);
        let offset = 0;
        for (const chunk of this.audioData) {
          mergedSamples.set(chunk, offset);
          offset += chunk.length;
        }

        // Encode to 16-bit PCM WAV
        const wavBuffer = this.encodeWAV(mergedSamples, this.sampleRate);
        const blob = new Blob([wavBuffer], { type: 'audio/wav' });
        const url = URL.createObjectURL(blob);

        resolve({ blob, url, durationSeconds });
      } catch (err) {
        reject(err);
      }
    });
  }

  private encodeWAV(samples: Float32Array, sampleRate: number): ArrayBuffer {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);

    /* RIFF identifier */
    this.writeString(view, 0, 'RIFF');
    /* file length */
    view.setUint32(4, 36 + samples.length * 2, true);
    /* RIFF type & format */
    this.writeString(view, 8, 'WAVE');
    /* format chunk identifier */
    this.writeString(view, 12, 'fmt ');
    /* format chunk length */
    view.setUint32(16, 16, true);
    /* sample format (raw PCM) */
    view.setUint16(20, 1, true);
    /* channel count (1 - Mono) */
    view.setUint16(22, 1, true);
    /* sample rate */
    view.setUint32(24, sampleRate, true);
    /* byte rate (sample rate * block align) */
    view.setUint32(28, sampleRate * 2, true);
    /* block align (channels * bytes per sample) */
    view.setUint16(32, 2, true);
    /* bits per sample (16-bit) */
    view.setUint16(34, 16, true);
    /* data chunk identifier */
    this.writeString(view, 36, 'data');
    /* data chunk length */
    view.setUint32(40, samples.length * 2, true);

    // Write PCM samples (float to int16)
    let index = 44;
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(index, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      index += 2;
    }

    return buffer;
  }

  private writeString(view: DataView, offset: number, string: string) {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  }
}
