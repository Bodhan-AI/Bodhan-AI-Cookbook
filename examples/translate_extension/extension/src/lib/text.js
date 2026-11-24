// Splits long text into API-sized chunks without breaking sentences where possible.

// Sentence boundary: whitespace after . ! ? or the Indic danda (। ॥) / Arabic ؟.
const SENTENCE_BOUNDARY = /(?<=[.!?।॥؟])\s+/u;

/**
 * Returns paragraphs, each an array of chunks no longer than maxChars.
 * Paragraph breaks are kept so the translation can be re-joined with the same shape.
 */
export function chunkText(text, maxChars) {
  if (!Number.isInteger(maxChars) || maxChars < 1) {
    throw new RangeError('maxChars must be a positive integer');
  }
  return text
    .replace(/\r\n?/g, '\n')
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => pack(splitParagraph(p, maxChars), maxChars));
}

export function joinChunks(paragraphs) {
  return paragraphs.map((chunks) => chunks.join(' ')).join('\n\n');
}

function splitParagraph(paragraph, maxChars) {
  if (paragraph.length <= maxChars) return [paragraph];
  return paragraph
    .split(SENTENCE_BOUNDARY)
    .flatMap((s) => (s.length <= maxChars ? [s] : splitOnSpaces(s, maxChars)));
}

// Last resort for a single sentence longer than maxChars.
function splitOnSpaces(sentence, maxChars) {
  const out = [];
  let rest = sentence;
  while (rest.length > maxChars) {
    let cut = rest.lastIndexOf(' ', maxChars);
    if (cut <= 0) cut = maxChars;
    out.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) out.push(rest);
  return out;
}

// Greedily merges sentences back together so we make as few API calls as possible.
function pack(pieces, maxChars) {
  const chunks = [];
  let current = '';
  for (const piece of pieces) {
    if (!current) current = piece;
    else if (current.length + 1 + piece.length <= maxChars) current += ' ' + piece;
    else {
      chunks.push(current);
      current = piece;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

/**
 * Splits text for reading aloud. Bodhan makes speech about as fast as it plays, so the
 * first segment is kept short to start sound sooner; the rest are fetched during playback.
 */
export function speechSegments(text, { first = 100, rest = 200 } = {}) {
  const [head, ...tail] = chunkText(text, rest).flat();
  if (head === undefined) return [];
  return [...chunkText(head, first).flat(), ...tail];
}
