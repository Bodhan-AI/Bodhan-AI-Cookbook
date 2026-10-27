import type { Language } from "../api";

export function languageName(code: string | null, languages: Language[]): string {
  if (!code) return "Auto-detected";
  return languages.find((l) => l.code === code)?.name ?? code;
}
