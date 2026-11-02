"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BhashaMark, BodhanBrand, CopyButton } from "./CompactVoiceInput";
import useTranslation from "./useTranslation";
import { fieldSelectionRect, replaceSelection, sameSelection, selectedField, selectionPosition } from "./selection.mjs";
import { TRANSLATION_LANGUAGES, isTranslationLanguage } from "./languages.mjs";
import styles from "./Compact.module.css";
import { animateCardOpening } from "./cardMotion.mjs";

function TranslationResult({ selection, target, onTargetChange, apiBaseUrl, showCopyButtons, onClose }) {
  const translation = useTranslation(selection.text, target, apiBaseUrl);
  const [error, setError] = useState("");
  const started = useRef(false);
  const translate = translation.translate;
  // This component mounts only after the user activates the launcher. Deferring
  // past effect cleanup avoids duplicate requests during Strict Mode's replay.
  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active || started.current) return;
      started.current = true;
      void translate();
    });
    return () => { active = false; };
  }, [translate]);

  function replace() {
    try { replaceSelection(selection, translation.text); onClose(); }
    catch (cause) { setError(cause.message); }
  }
  return <>
    <div className={styles.comparison}>
      <section className={styles.comparisonSource} aria-label="Original text">
        <div className={styles.comparisonHeading}><span>Original</span></div>
        <p className={styles.comparisonText} dir="auto">{selection.text}</p>
      </section>
      <section className={styles.comparisonTranslation} aria-label="Translated text">
        <div className={styles.comparisonHeading}>
          <label htmlFor={`target-${selection.id}`}>Translation</label>
          <select id={`target-${selection.id}`} aria-label="Translate to" value={target} onChange={e => onTargetChange(e.target.value)}>{TRANSLATION_LANGUAGES.map(({ code, label }) => <option key={code} value={code}>{label}</option>)}</select>
        </div>
        {["idle", "loading"].includes(translation.status) && selection.text.length <= 2000 && <p role="status" className={styles.translating}><span className={styles.spinner} /> Translating…</p>}
        {translation.status === "success" && <p className={styles.comparisonText} dir="auto" lang={target}>{translation.text}</p>}
        {translation.status === "error" && <button type="button" className={styles.primary} onClick={translate} disabled={translation.retryAfterSeconds > 0}>Retry translation</button>}
        {selection.text.length > 2000 && <p className={styles.error}>Select up to 2,000 characters to translate.</p>}
        {translation.retryAfterSeconds > 0 && <p className={styles.hint}>Please wait {translation.retryAfterSeconds} seconds before retrying.</p>}
        {translation.error && <p role="alert" className={styles.error}>{translation.error}</p>}
      </section>
    </div>
    {(showCopyButtons || (translation.status === "success" && selection.editable)) && <div className={styles.actions}>
      {showCopyButtons && <CopyButton text={selection.text} label="Copy original" />}
      {translation.status === "success" && <>{showCopyButtons && <CopyButton text={translation.text} label="Copy translation" />}{selection.editable && <button type="button" className={styles.primary} onClick={replace}>Replace selection</button>}</>}
    </div>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
  </>;
}

function SelectionPopover({ selection, apiBaseUrl, showCopyButtons, defaultTargetLanguage, onClose }) {
  const [expanded, setExpanded] = useState(false);
  const [target, setTarget] = useState(isTranslationLanguage(defaultTargetLanguage) ? defaultTargetLanguage : "en");
  const popoverRef = useRef(null);
  const panelRef = useRef(null);
  const launchRectRef = useRef(null);
  useLayoutEffect(() => {
    const element = popoverRef.current;
    function place() {
      const content = element.firstElementChild;
      const height = expanded ? Math.min(470, content.scrollHeight + 2) : element.offsetHeight;
      const position = selectionPosition(selection.anchor, element.offsetWidth, height,
        { width: window.innerWidth, height: window.innerHeight }, expanded ? "bottom" : "top");
      element.style.left = `${position.left}px`;
      element.style.top = `${position.top}px`;
      element.dataset.side = position.side;
      element.style.setProperty("--available-height", `${position.maxHeight}px`);
      element.style.setProperty("--selection-origin", position.origin);
    }
    place();
    const stopOpening = expanded ? animateCardOpening(panelRef.current, launchRectRef.current) : () => {};
    const observer = new ResizeObserver(place);
    observer.observe(element);
    const contentObserver = new MutationObserver(place);
    contentObserver.observe(element, { childList: true, characterData: true, subtree: true });
    return () => { stopOpening(); observer.disconnect(); contentObserver.disconnect(); };
  }, [selection, expanded]);
  useEffect(() => { if (expanded) panelRef.current?.focus({ preventScroll: true }); }, [expanded]);
  return <aside ref={popoverRef} data-bhasha-ui className={`${styles.selection} ${expanded ? styles.selectionExpanded : ""}`} aria-label="BhashaVoice translation">
    {!expanded ? <button type="button" className={styles.selectionTrigger} onPointerDown={e => e.preventDefault()} onClick={event => { launchRectRef.current = event.currentTarget.getBoundingClientRect(); setExpanded(true); }} aria-label="Translate selected text" title="Translate selected text"><BhashaMark /><span aria-hidden="true">文↔A</span></button> : <div ref={panelRef} tabIndex={-1} className={styles.translationCard}>
      <header className={styles.cardHeader}><span><BhashaMark /> Translate selection</span><button type="button" onClick={onClose} aria-label="Close translation">×</button></header>
      <TranslationResult key={`${target}:${apiBaseUrl}`} selection={selection} target={target} onTargetChange={setTarget} apiBaseUrl={apiBaseUrl} showCopyButtons={showCopyButtons} onClose={onClose} />
      <BodhanBrand />
    </div>}
  </aside>;
}

// Mount once per document. Password fields and Bhasha controls are excluded.
export default function SelectionTranslator({ apiBaseUrl = "/api/bhasha", showCopyButtons = true, defaultTargetLanguage = "en" }) {
  const [selection, setSelection] = useState(null);
  const sequence = useRef(0);
  useEffect(() => {
    let interactingWithPopover = false;
    function insideUI(target) { return Boolean(target?.closest?.("[data-bhasha-ui]")); }
    function capture(event) {
      // A textarea can emit a delayed `select` when the launcher is pressed.
      // Preserve its snapshot through the entire gesture, before click fires.
      if (interactingWithPopover || insideUI(event.target) || insideUI(document.activeElement)) return;
      if (event.type === "select" && event.target !== document.activeElement) return;
      if (event.target?.closest?.("[data-bhasha-ignore]")) return;
      if (event.type === "keyup" && !["Shift", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "a", "A"].includes(event.key)) return;
      let snapshot = selectedField(document.activeElement);
      let anchor;
      if (snapshot) anchor = fieldSelectionRect(snapshot);
      else {
        const native = window.getSelection();
        if (!native || native.isCollapsed || !native.rangeCount || !native.toString().trim()) {
          setSelection(null);
          return;
        }
        const range = native.getRangeAt(0).cloneRange();
        const element = range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
        if (element?.closest('[data-bhasha-ui], [data-bhasha-ignore], input, textarea')) return;
        const editor = element?.closest('[contenteditable="true"], [contenteditable="plaintext-only"]');
        snapshot = { text: range.toString(), range, editor, editable: Boolean(editor && editor.contains(range.startContainer) && editor.contains(range.endContainer)), value: editor?.textContent };
        const rects = Array.from(range.getClientRects()).filter(rect => rect.width && rect.height && rect.bottom > 0 && rect.top < window.innerHeight);
        const backward = native.focusNode === range.startContainer && native.focusOffset === range.startOffset;
        anchor = backward ? rects[0] : rects.at(-1);
      }
      if (!anchor) return;
      const next = { ...snapshot, anchor, id: ++sequence.current };
      setSelection(previous => sameSelection(previous, next) ? previous : next);
    }
    function pointerDown(event) {
      interactingWithPopover = insideUI(event.target);
      if (!interactingWithPopover) setSelection(null);
    }
    function keyDown(event) {
      interactingWithPopover = insideUI(event.target);
      if (event.key === "Escape") setSelection(null);
    }
    function focusIn(event) {
      interactingWithPopover = insideUI(event.target);
    }
    function dismiss(event) {
      if (insideUI(event.target)) return;
      setSelection(null);
    }
    document.addEventListener("pointerup", capture);
    document.addEventListener("select", capture);
    document.addEventListener("keyup", capture);
    document.addEventListener("pointerdown", pointerDown, true);
    document.addEventListener("keydown", keyDown);
    document.addEventListener("focusin", focusIn);
    document.addEventListener("input", dismiss);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      document.removeEventListener("select", capture);
      document.removeEventListener("pointerup", capture);
      document.removeEventListener("keyup", capture);
      document.removeEventListener("pointerdown", pointerDown, true);
      document.removeEventListener("keydown", keyDown);
      document.removeEventListener("focusin", focusIn);
      document.removeEventListener("input", dismiss);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  }, []);
  return selection?.anchor ? createPortal(<SelectionPopover key={selection.id} selection={selection} apiBaseUrl={apiBaseUrl} showCopyButtons={showCopyButtons} defaultTargetLanguage={defaultTargetLanguage} onClose={() => setSelection(null)} />, document.body) : null;
}
