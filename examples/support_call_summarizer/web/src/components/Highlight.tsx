import { Fragment, type ReactNode } from "react";

/** Wraps case-insensitive occurrences of `query` in <mark>. */
export function Highlight({ text, query }: { text: string; query: string }) {
  const q = query.trim();
  if (!q) return <>{text}</>;

  const lowerText = text.toLocaleLowerCase();
  const lowerQuery = q.toLocaleLowerCase();
  // Lower-casing can change string length for some characters; fall back to plain text then.
  if (lowerText.length !== text.length) return <>{text}</>;

  const parts: ReactNode[] = [];
  let from = 0;
  let hit = lowerText.indexOf(lowerQuery, from);
  while (hit !== -1) {
    if (hit > from) parts.push(<Fragment key={`t${from}`}>{text.slice(from, hit)}</Fragment>);
    parts.push(<mark key={`m${hit}`}>{text.slice(hit, hit + lowerQuery.length)}</mark>);
    from = hit + lowerQuery.length;
    hit = lowerText.indexOf(lowerQuery, from);
  }
  if (from < text.length) parts.push(<Fragment key={`t${from}`}>{text.slice(from)}</Fragment>);
  return <>{parts}</>;
}
