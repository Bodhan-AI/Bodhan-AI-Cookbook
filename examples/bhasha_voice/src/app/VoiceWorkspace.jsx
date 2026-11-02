"use client";
import { useLayoutEffect, useRef, useState } from "react";
import { CompactVoiceInput, SelectionTranslator } from "@bhashavoice/react";
import styles from "./page.module.css";

export default function VoiceWorkspace() {
  const [text, setText] = useState("");
  const fieldRef = useRef(null);
  const selectionRef = useRef({ start: 0, end: 0 });
  const pendingCursor = useRef(null);
  useLayoutEffect(() => {
    if (pendingCursor.current == null) return;
    fieldRef.current?.focus();
    fieldRef.current?.setSelectionRange(pendingCursor.current, pendingCursor.current);
    pendingCursor.current = null;
  }, [text]);
  function insert(value) {
    const field = fieldRef.current;
    const { start, end } = selectionRef.current;
    pendingCursor.current = start + value.length;
    setText(field.value.slice(0, start) + value + field.value.slice(end));
    field.focus();
  }
  return <div className={styles.workspace}>
    <div className={styles.editorRow}>
    <div className={styles.descriptionField}>
      <label htmlFor="voice-text">Your text</label>
      <textarea ref={fieldRef} id="voice-text" value={text} onChange={e=>setText(e.target.value)} onSelect={e=>{ selectionRef.current = { start:e.currentTarget.selectionStart, end:e.currentTarget.selectionEnd }; }} placeholder="A thought, a message, a little inspiration…" rows={6} dir="auto" />
    </div>
    <div className={styles.voiceDock}><CompactVoiceInput onInsert={insert} /></div>
    </div>
    <p className={styles.note}>Stop to transcribe with Bodhan. Cancel to discard. Your writing clears on refresh.</p>
    <div className={styles.reading}>
      <p lang="en">Every morning, the city wakes up to familiar sounds: a kettle boiling, a bicycle bell, and neighbours greeting each other. We may speak different languages, but the stories we share bring us closer. A simple conversation can turn a stranger into a friend.</p>
      <p lang="hi">हर सुबह शहर जानी-पहचानी आवाज़ों के साथ जागता है। कहीं चाय बन रही होती है, कहीं साइकिल की घंटी बजती है और कहीं पड़ोसी एक-दूसरे का हाल पूछते हैं। हमारी भाषाएँ अलग हो सकती हैं, लेकिन छोटी-छोटी बातें हमें जोड़ती हैं। कभी-कभी एक बातचीत से नई दोस्ती की शुरुआत होती है।</p>
    </div>
    <SelectionTranslator showCopyButtons={false} defaultTargetLanguage="hi" />
  </div>;
}
