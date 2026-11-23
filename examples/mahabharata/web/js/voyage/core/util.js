// Small DOM + math helpers used across the voyage.

/** querySelector shorthand (root defaults to document). */
export const $ = (sel, root = document) => root.querySelector(sel);

/** querySelectorAll as a real array. */
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** Build ONE element from an HTML string (first element child). Callers must esc() untrusted text. */
export function el(html) {
  const t = document.createElement('template');
  t.innerHTML = String(html).trim();
  return t.content.firstElementChild;
}

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
/** Escape text for safe interpolation into HTML (null/undefined -> ''). */
export function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v).replace(/[&<>"']/g, (c) => ESC[c]);
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));

export const lerp = (a, b, t) => a + (b - a) * t;

/**
 * Effective reduced-motion preference.
 * main.js mirrors the user's setting onto <body>: class `reduced-motion` (forced on) or
 * data-motion="full" (forced off). Otherwise the OS preference decides.
 */
export function prefersReducedMotion() {
  const b = typeof document !== 'undefined' ? document.body : null;
  if (b) {
    if (b.classList.contains('reduced-motion')) return true;
    if (b.dataset && b.dataset.motion === 'full') return false;
  }
  try {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  } catch {
    return false;
  }
}

/** Seconds -> "12.4 s" / "1 m 05 s". */
export function fmtSecs(s) {
  const n = Number(s);
  if (!Number.isFinite(n) || n < 0) return '';
  if (n < 60) return `${n < 10 ? n.toFixed(1) : Math.round(n)} s`;
  const m = Math.floor(n / 60);
  const r = Math.round(n % 60);
  return `${m} m ${String(r).padStart(2, '0')} s`;
}

/** "kamyaka_forest" -> "Kamyaka Forest" (only used when a display name is missing). */
export function humanize(id) {
  return String(id || '')
    .replace(/^(char|obj|arch|env)\./, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Accept only plain CSS hex colours from data before writing them into styles. */
export function safeHex(v, fallback = null) {
  return typeof v === 'string' && /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(v.trim()) ? v.trim() : fallback;
}

/** Integer -> Roman numeral (1..3999); used for chapter roundels. */
export function roman(n) {
  let x = Math.floor(Number(n));
  if (!Number.isFinite(x) || x <= 0 || x >= 4000) return String(n ?? '');
  const map = [[1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'],
    [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']];
  let out = '';
  for (const [v, s] of map) while (x >= v) { out += s; x -= v; }
  return out;
}

/** True when the keyboard event originates from a text-entry control. */
export function isTypingTarget(t) {
  if (!t || !(t instanceof Element)) return false;
  if (t.isContentEditable) return true;
  const tag = t.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = (t.getAttribute('type') || 'text').toLowerCase();
    return !['button', 'checkbox', 'radio', 'range', 'submit', 'reset', 'color'].includes(type);
  }
  return false;
}
