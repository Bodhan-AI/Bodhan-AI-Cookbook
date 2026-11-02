// Keep a snapshot: replacing text must never overwrite intervening edits.
export function selectedField(field) {
  const tag = field?.tagName;
  if (tag !== "TEXTAREA" && !(tag === "INPUT" && ["text", "search", "tel", "url"].includes(field.type))) return null;
  const start = field.selectionStart, end = field.selectionEnd;
  if (start == null || end == null || end <= start) return null;
  const text = field.value.slice(start, end);
  if (!text.trim()) return null;
  return { text, field, start, end, value: field.value, editable: !field.readOnly && !field.disabled };
}

// Text controls don't expose Range rectangles. Mirror their text layout without
// changing focus or the live selection, including wrapping and internal scroll.
export function fieldSelectionRect(snapshot) {
  const { field, start, end } = snapshot;
  const doc = field.ownerDocument;
  const css = doc.defaultView.getComputedStyle(field);
  const box = field.getBoundingClientRect();
  const mirror = doc.createElement("div");
  const properties = [
    "fontFamily", "fontSize", "fontWeight", "fontStyle", "fontVariant",
    "fontStretch", "fontKerning", "fontFeatureSettings", "lineHeight",
    "letterSpacing", "wordSpacing", "textAlign", "textIndent", "textTransform",
    "direction", "tabSize", "wordBreak", "overflowWrap", "paddingTop",
    "paddingRight", "paddingBottom", "paddingLeft", "borderTopWidth",
    "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
  ];
  for (const property of properties) mirror.style[property] = css[property];
  Object.assign(mirror.style, {
    position: "fixed", left: `${box.left}px`, top: `${box.top}px`,
    width: `${field.clientWidth + parseFloat(css.borderLeftWidth) + parseFloat(css.borderRightWidth)}px`,
    height: `${box.height}px`, boxSizing: "border-box", borderStyle: "solid",
    visibility: "hidden", pointerEvents: "none", overflow: "hidden",
    whiteSpace: field.tagName === "INPUT" || field.wrap === "off" ? "pre" : "pre-wrap",
  });
  mirror.setAttribute("aria-hidden", "true");
  mirror.setAttribute("data-bhasha-ui", "");
  const selected = doc.createElement("span");
  selected.textContent = snapshot.text;
  mirror.append(doc.createTextNode(snapshot.value.slice(0, start)), selected,
    doc.createTextNode(snapshot.value.slice(end) || "\u200b"));
  doc.body.append(mirror);
  try {
    mirror.scrollTop = field.scrollTop;
    mirror.scrollLeft = field.scrollLeft;
    const rects = Array.from(selected.getClientRects());
    const visible = rects.filter(rect => rect.bottom > box.top && rect.top < box.bottom);
    const rect = field.selectionDirection === "backward" ? visible[0] : visible.at(-1);
    if (!rect) return null;
    return {
      left: Math.max(box.left, rect.left), right: Math.min(box.right, rect.right),
      top: Math.max(box.top, rect.top), bottom: Math.min(box.bottom, rect.bottom),
    };
  } finally {
    mirror.remove();
  }
}

export function selectionPosition(anchor, width, height, viewport, preferred = "bottom") {
  const margin = 12, gap = 8;
  const clamp = (value, min, max) => Math.max(min, Math.min(value, max));
  const fullHeight = Math.max(0, viewport.height - margin * 2);
  const right = anchor.right ?? anchor.left;
  const candidates = {
    bottom: { space: viewport.height - anchor.bottom - gap - margin, horizontal: true },
    top: { space: anchor.top - gap - margin, horizontal: true },
    right: { space: fullHeight, fitsWidth: viewport.width - right - gap - margin >= width },
    left: { space: fullHeight, fitsWidth: anchor.left - gap - margin >= width },
  };
  const order = [...new Set([preferred, "bottom", "top", "right", "left"])];
  const usable = order.filter(side => candidates[side].horizontal || candidates[side].fitsWidth);
  const side = usable.find(side => candidates[side].space >= height) ??
    usable.reduce((best, side) => candidates[side].space > candidates[best].space ? side : best);
  const available = Math.max(0, candidates[side].space);
  const renderedHeight = Math.min(height, available);
  const left = side === "right" ? right + gap : side === "left" ? anchor.left - gap - width : anchor.left;
  const top = side === "bottom" ? anchor.bottom + gap : side === "top" ? anchor.top - gap - renderedHeight : (anchor.top + anchor.bottom - renderedHeight) / 2;
  return {
    left: clamp(left, margin, viewport.width - width - margin),
    top: clamp(top, margin, viewport.height - renderedHeight - margin),
    maxHeight: available,
    origin: side === "top" ? "bottom left" : side === "left" ? "center right" : side === "right" ? "center left" : "top left",
    side,
  };
}

export function sameSelection(a, b) {
  if (!a || !b || a.text !== b.text) return false;
  if (a.field || b.field) return a.field === b.field && a.start === b.start && a.end === b.end && a.value === b.value;
  return a.range.startContainer === b.range.startContainer && a.range.startOffset === b.range.startOffset &&
    a.range.endContainer === b.range.endContainer && a.range.endOffset === b.range.endOffset;
}
export function replacementValue(snapshot, translation) {
  if (!snapshot.field.isConnected || snapshot.field.value !== snapshot.value) throw new Error("The field changed. Select the text again before replacing it.");
  return snapshot.value.slice(0, snapshot.start) + translation + snapshot.value.slice(snapshot.end);
}
export function replaceSelection(snapshot, translation) {
  if (!snapshot.editable) throw new Error("This text is read-only. Use Copy instead.");
  if (snapshot.field) {
    const value = replacementValue(snapshot, translation);
    const field = snapshot.field;
    const prototype = field.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.focus();
    field.setSelectionRange(snapshot.start, snapshot.start + translation.length);
  } else {
    const { range, editor } = snapshot;
    if (!editor?.isConnected || editor.textContent !== snapshot.value || range.toString() !== snapshot.text) throw new Error("The text changed. Select it again before replacing it.");
    range.deleteContents();
    const node = document.createTextNode(translation);
    range.insertNode(node);
    editor.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: translation }));
    editor.focus();
  }
}
