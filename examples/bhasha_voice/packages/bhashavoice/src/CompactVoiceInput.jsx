"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { TRANSCRIPTION_LANGUAGES, isTranscriptionLanguage } from "./languages.mjs";
import VoiceIcon from "./VoiceIcon";
import useVoiceRecorder from "./useVoiceRecorder";
import useTranscription from "./useTranscription";
import useTranscriptReview from "./useTranscriptReview";
import styles from "./Compact.module.css";
import bodhanLogo from "./assets/bodhan-logo.png";
import { selectionPosition } from "./selection.mjs";
import { animateCardOpening } from "./cardMotion.mjs";

export function BhashaMark() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 8v8M10 4v16M15 7v10M20 10v4" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" /></svg>;
}

export function BodhanBrand() {
  return <footer className={styles.branding}>Powered by <picture><img src={typeof bodhanLogo === "string" ? bodhanLogo : bodhanLogo.src} width="16" height="16" alt="" /></picture><strong>Bodhan</strong></footer>;
}

export function CopyButton({ text, label = "Copy" }) {
  const [message, setMessage] = useState("");
  async function copy() {
    try { await navigator.clipboard.writeText(text); setMessage("Copied"); }
    catch { setMessage("Select the text and copy manually."); }
  }
  return <><button type="button" onClick={copy} disabled={!text.trim()}>{label}</button><span className={styles.feedback} role="status">{message}</span></>;
}

function TranscriptCard({ text, onInsert, onDiscard }) {
  const review = useTranscriptReview(text, onInsert);
  const [editing, setEditing] = useState(false);
  return <div className={styles.transcriptCard}>
    <header className={styles.cardHeader}><span><BhashaMark /> Your words</span><button type="button" onClick={onDiscard} aria-label="Discard transcript">×</button></header>
    {editing ? <textarea dir="auto" aria-label="Edit transcript" value={review.text} onChange={e => review.edit(e.target.value)} readOnly={review.status !== "review"} rows={4} autoFocus /> : <p className={styles.result} dir="auto">{review.text}</p>}
    <div className={styles.actions}>
      <button type="button" className={styles.primary} disabled={!review.canInsert} onClick={() => review.insert()}>{review.status === "inserted" ? "Inserted" : review.status === "inserting" ? "Inserting…" : "Insert"}</button>
      <button type="button" disabled={review.status !== "review"} onClick={() => setEditing(!editing)}>{editing ? "Done editing" : "Edit"}</button>
      <CopyButton key={review.text} text={review.text} />
    </div>
    {review.error && <p role="alert" className={styles.error}>{review.error}</p>}
    <BodhanBrand />
  </div>;
}

export default function CompactVoiceInput({ onInsert, defaultLanguage = "en", apiBaseUrl = "/api/bhasha" }) {
  const anchorRef = useRef(null);
  const idleToolsRef = useRef(null);
  const launchRectRef = useRef(null);
  const recorder = useVoiceRecorder();
  const [history, setHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [language, setLanguage] = useState(isTranscriptionLanguage(defaultLanguage) ? defaultLanguage : "en");
  const transcription = useTranscription(recorder.audioBlob, language, apiBaseUrl);
  const recording = recorder.status === "recording";
  const waiting = recorder.status === "requesting" || recorder.status === "stopping";
  const loading = transcription.status === "loading" || (Boolean(recorder.audioBlob) && transcription.status === "idle");
  const ready = Boolean(recorder.audioBlob);
  const expanded = transcription.status === "success";
  useLayoutEffect(() => {
    const tools = idleToolsRef.current;
    if (!tools) return;
    const measure = () => tools.parentElement.style.setProperty("--tools-width", `${tools.scrollWidth}px`);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(tools);
    return () => observer.disconnect();
  }, [recording, ready, waiting, expanded, showHistory]);
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    const content = anchor.firstElementChild;
    function place() {
      const box = anchor.getBoundingClientRect();
      let position = { left: box.left, top: box.top, maxHeight: Math.max(48, window.innerHeight - box.top - 12) };
      if (expanded || showHistory) {
        position = selectionPosition({ left: box.left, right: box.right, top: box.top, bottom: box.top },
          content.offsetWidth, Math.min(470, content.scrollHeight), { width: window.innerWidth, height: window.innerHeight });
      }
      content.style.left = `${position.left - box.left}px`;
      content.style.top = `${position.top - box.top}px`;
      anchor.style.setProperty("--voice-available-height", `${position.maxHeight}px`);
      if (!expanded && !showHistory) launchRectRef.current = content.getBoundingClientRect();
    }
    place();
    const stopOpening = expanded || showHistory ? animateCardOpening(content.firstElementChild, launchRectRef.current) : () => {};
    const observer = new ResizeObserver(place);
    observer.observe(content);
    observer.observe(anchor.parentElement);
    const mutations = new MutationObserver(place);
    mutations.observe(content, { childList: true, characterData: true, subtree: true });
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      stopOpening();
      observer.disconnect();
      mutations.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [expanded, showHistory]);
  const submitted = useRef(null);
  const audio = recorder.audioBlob;
  const transcribe = transcription.transcribe;
  useEffect(() => {
    if (!audio || submitted.current === audio) return;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      submitted.current = audio;
      void transcribe();
    });
    return () => { active = false; };
  }, [audio, transcribe]);
  async function insert(text) {
    await onInsert(text);
    setHistory(previous => [text, ...previous].slice(0, 5));
    recorder.discard();
  }
  return <div ref={anchorRef} className={styles.anchor}><div data-bhasha-ui className={`${styles.voice} ${expanded || showHistory ? styles.expanded : ""}`}>
    {showHistory ? <div className={styles.transcriptCard}>
      <header className={styles.cardHeader}><span><BhashaMark /> Recent voice text</span><button type="button" onClick={()=>setShowHistory(false)} aria-label="Close voice history">×</button></header>
      {history.map((text,index)=><div key={index} className={styles.historyItem}><p className={styles.result} dir="auto">{text}</p><div className={styles.actions}><CopyButton text={text} /></div></div>)}
      <p className={styles.hint}>Last five inserts · cleared when this page closes.</p><BodhanBrand />
    </div> : expanded ? <TranscriptCard key={recorder.audioUrl} text={transcription.text} onInsert={onInsert ? insert : undefined} onDiscard={recorder.discard} /> : <>
      <div className={`${styles.pill} ${!recording && !ready && !waiting ? styles.idlePill : ""}`}>
        <button type="button" className={`${styles.mic} ${recording ? styles.recording : ""}`} onClick={recording ? recorder.stop : ready ? transcription.transcribe : recorder.start} disabled={waiting || loading || transcription.retryAfterSeconds > 0}
          aria-label={recording ? "Stop recording" : ready ? "Retry transcription" : "Record with BhashaVoice"} title={recording ? "Stop recording" : ready ? "Retry transcription" : "Record with BhashaVoice"}>
          {loading || waiting ? <span className={styles.spinner} /> : recording ? <span className={styles.stop} /> : <VoiceIcon />}
          {!recording && !loading && !waiting && <span className={styles.miniMark}><BhashaMark /></span>}
        </button>
        {recording ? <><span className={styles.liveBars} aria-hidden="true" title={recorder.audioActivity.available ? "Live microphone level" : "Live microphone level unavailable"}>{recorder.audioActivity.levels.map((level, index) => <i key={index} style={{ height: `${3 + level * 13}px` }} />)}</span><span className={styles.time}>{recorder.elapsedSeconds}s / 25s</span></> : loading || waiting ? <span className={styles.pillLabel}>{loading ? "Transcribing" : recorder.status === "requesting" ? "Allow microphone" : "Finishing"}</span> : ready ? <button type="button" className={styles.transcribe} onClick={transcription.transcribe} disabled={transcription.retryAfterSeconds > 0}>Retry</button> : <div className={styles.idleTools}><div ref={idleToolsRef} className={styles.idleToolsInner}><select aria-label="Spoken language" value={language} onChange={e=>setLanguage(e.target.value)}>{TRANSCRIPTION_LANGUAGES.map(({ code, label }) => <option key={code} value={code}>{label}</option>)}</select>{history.length > 0 && <button className={styles.historyButton} type="button" onClick={()=>setShowHistory(true)} aria-label="Recent voice text" title="Recent voice text"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.6"/><path d="M12 7v5l3 2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg></button>}</div></div>}
        {(recording || ready || waiting) && <button type="button" className={styles.dismiss} onClick={recorder.discard} aria-label="Discard recording">×</button>}
      </div>
      {ready && !loading && <details className={styles.playback}><summary>Listen back</summary><audio controls src={recorder.audioUrl} /></details>}
      <span className={styles.srOnly} role="status">{recording ? "Recording. Press Stop when finished." : loading ? "Transcribing recording." : ready ? "Recording ready to transcribe." : ""}</span>
      {(recorder.error || transcription.error) && <p className={styles.error} role="alert">{recorder.error || transcription.error}</p>}
      {transcription.retryAfterSeconds > 0 && <p role="status" className={styles.hint}>Retry available after {transcription.retryAfterSeconds} seconds.</p>}
    </>}
  </div></div>;
}
