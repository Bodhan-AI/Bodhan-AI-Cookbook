import { chunkText, joinChunks } from './text.js';

/**
 * Translates text of any length by chunking it and calling the client once per chunk.
 * Chunks are sent one at a time to stay well inside API rate limits.
 */
export async function translateText(client, { text, target, script, maxChars }) {
  let truncated = false;
  const translated = [];
  for (const paragraph of chunkText(text, maxChars)) {
    const out = [];
    for (const chunk of paragraph) {
      const result = await client.translate({ text: chunk, target, script });
      truncated ||= result.truncated;
      out.push(result.text);
    }
    translated.push(out);
  }
  return { text: joinChunks(translated), truncated };
}
