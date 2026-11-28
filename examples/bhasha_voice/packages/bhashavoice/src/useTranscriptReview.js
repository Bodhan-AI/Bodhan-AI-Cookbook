"use client";

import { useEffect, useRef, useState } from "react";

// The review component mounts a fresh instance for each recording/language.
export default function useTranscriptReview(originalText, onInsert) {
  const [text, setText] = useState(originalText);
  const [status, setStatus] = useState("review");
  const [error, setError] = useState("");
  const lockedRef = useRef(false);
  const activeRef = useRef(false);

  useEffect(() => {
    activeRef.current = true;
    return () => { activeRef.current = false; };
  }, []);

  function edit(value) {
    if (lockedRef.current) return;
    setText(value);
    setError("");
  }

  async function insert(selectedText = text) {
    if (lockedRef.current || typeof selectedText !== "string" || !selectedText.trim() || typeof onInsert !== "function") return;
    // Lock immediately, including repeated clicks before the next React render.
    lockedRef.current = true;
    setStatus("inserting");
    setError("");
    try {
      await onInsert(selectedText);
      if (activeRef.current) setStatus("inserted");
    } catch {
      if (!activeRef.current) return;
      lockedRef.current = false;
      setStatus("review");
      setError("Text could not be inserted. Your edits are still here. Please try again.");
    }
  }

  return {
    text, status, error, edit, insert,
    canInsert: status === "review" && Boolean(text.trim()) && typeof onInsert === "function",
  };
}
