"use client";

import { useEffect, useRef, useState } from "react";
import { startMicrophoneActivity } from "./microphoneActivity.mjs";

const MAX_SECONDS = 25;
const MAX_DURATION_MS = MAX_SECONDS * 1000;
const NO_ACTIVITY = { available: false, levels: [0, 0, 0, 0] };

function clearSessionTimers(session) {
  clearInterval(session.tickTimer);
  clearTimeout(session.limitTimer);
  session.tickTimer = null;
  session.limitTimer = null;
}

function elapsedTime(session) {
  return Math.min(MAX_SECONDS, Math.floor((performance.now() - session.startedAt) / 1000));
}

function startSessionTimers(session, updateElapsed, onLimit) {
  session.startedAt = performance.now();
  session.tickTimer = setInterval(() => {
    if (session.closed || session.stopping) return;
    updateElapsed(elapsedTime(session));
    if (performance.now() - session.startedAt >= MAX_DURATION_MS) onLimit();
  }, 200);
  session.limitTimer = setTimeout(onLimit, MAX_DURATION_MS);
}

function releaseSession(session) {
  if (!session) return;

  session.closed = true;
  clearSessionTimers(session);
  session.stopActivity?.();
  if (session.recorder) {
    session.recorder.onstop = null;
    session.recorder.onerror = null;
    session.recorder.ondataavailable = null;
    if (session.recorder.state !== "inactive") {
      try {
        session.recorder.stop();
      } catch {
        // A device failure may already have ended the recorder.
      }
    }
  }
  session.stream?.getTracks().forEach((track) => track.stop());
  session.chunks = [];
}

function microphoneError(error) {
  switch (error?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Microphone access was blocked. Allow it in your browser or device settings, then try again.";
    case "NotFoundError":
      return "No microphone was found. Connect a microphone and try again.";
    case "NotReadableError":
      return "Your microphone could not be opened. Check whether another app is using it, then try again.";
    default:
      return "Recording could not start. Check your microphone and try again.";
  }
}

export default function useVoiceRecorder() {
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [audioBlob, setAudioBlob] = useState(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [reachedLimit, setReachedLimit] = useState(false);
  const [audioActivity, setAudioActivity] = useState(NO_ACTIVITY);
  const sessionRef = useRef(null);
  const audioUrlRef = useRef("");

  useEffect(() => {
    return () => {
      releaseSession(sessionRef.current);
      sessionRef.current = null;
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = "";
    };
  }, []);

  async function start() {
    // Also guards against repeated clicks before React updates the button.
    if (sessionRef.current || audioUrlRef.current) return;
    setError("");
    setElapsedSeconds(0);
    setReachedLimit(false);
    setAudioActivity(NO_ACTIVITY);

    if (!window.isSecureContext) {
      setError("Microphone recording needs HTTPS or localhost.");
      setStatus("error");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError("This browser does not support voice recording. Try desktop Chrome.");
      setStatus("error");
      return;
    }

    const session = {
      stream: null, recorder: null, closed: false, stopping: false, chunks: [],
      startedAt: null, tickTimer: null, limitTimer: null,
    };
    sessionRef.current = session;
    setStatus("requesting");

    try {
      session.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Permission may arrive after this component has been removed.
      if (session.closed || sessionRef.current !== session) {
        releaseSession(session);
        return;
      }

      const recorder = new MediaRecorder(session.stream);
      session.recorder = recorder;
      recorder.ondataavailable = (event) => {
        if (sessionRef.current !== session || session.closed) return;
        if (event.data.size > 0) session.chunks.push(event.data);
      };
      recorder.onstop = () => {
        if (sessionRef.current !== session || session.closed) return;
        if (!session.stopping) setElapsedSeconds(elapsedTime(session));
        // The final dataavailable event precedes stop. Keep its actual format.
        try {
          const audio = new Blob(session.chunks, {
            type: recorder.mimeType || session.chunks[0]?.type || "",
          });
          if (!audio.size) throw new Error("Empty recording");
          const url = URL.createObjectURL(audio);
          releaseSession(session);
          sessionRef.current = null;
          audioUrlRef.current = url;
          setAudioBlob(audio);
          setAudioUrl(url);
          setStatus("stopped");
        } catch {
          releaseSession(session);
          sessionRef.current = null;
          setError("No audio preview could be created. Please record again.");
          setStatus("error");
        }
      };
      recorder.onerror = () => {
        if (sessionRef.current !== session || session.closed) return;
        releaseSession(session);
        sessionRef.current = null;
        setError("Recording was interrupted. Check your microphone and try again.");
        setStatus("error");
      };
      recorder.start();
      session.stopActivity = startMicrophoneActivity(session.stream, activity => {
        if (sessionRef.current === session && !session.closed && !session.stopping) setAudioActivity(activity);
      });
      startSessionTimers(session, setElapsedSeconds, () => finishRecording(session, true));
      setStatus("recording");
    } catch (cause) {
      releaseSession(session);
      if (sessionRef.current !== session) return;
      sessionRef.current = null;
      setError(microphoneError(cause));
      setStatus("error");
    }
  }

  function finishRecording(session, limited = false) {
    if (!session?.recorder || sessionRef.current !== session || session.closed || session.stopping) return;
    session.stopping = true;
    session.stopActivity?.();
    clearSessionTimers(session);
    setElapsedSeconds(limited ? MAX_SECONDS : elapsedTime(session));
    setReachedLimit(limited);
    setStatus("stopping");
    try {
      if (session.recorder.state !== "inactive") session.recorder.stop();
    } catch {
      releaseSession(session);
      sessionRef.current = null;
      setError("Recording could not be finished. Please record again.");
      setStatus("error");
    } finally {
      // Stop the mic now, but keep event handlers for the final audio chunks.
      session.stream?.getTracks().forEach((track) => track.stop());
    }
  }

  function stop() {
    finishRecording(sessionRef.current);
  }

  function discard() {
    releaseSession(sessionRef.current);
    sessionRef.current = null;
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    audioUrlRef.current = "";
    setAudioUrl("");
    setAudioBlob(null);
    setError("");
    setElapsedSeconds(0);
    setReachedLimit(false);
    setStatus("idle");
  }

  function cancel() {
    if (!sessionRef.current) return;
    discard();
  }

  return { status, error, audioUrl, audioBlob, elapsedSeconds, maxSeconds: MAX_SECONDS, reachedLimit, audioActivity: status === "recording" ? audioActivity : NO_ACTIVITY, start, stop, discard, cancel };
}
