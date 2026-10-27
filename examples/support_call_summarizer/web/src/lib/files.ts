import type { AppConfig } from "../api";
import { formatBytes } from "./format";

export function fileExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && dot < name.length - 1 ? name.slice(dot).toLowerCase() : "";
}

export function normaliseExtensions(exts: string[]): string[] {
  return exts.map((e) => (e.startsWith(".") ? e : `.${e}`).toLowerCase());
}

/** Returns a user-facing error message, or null when the file looks acceptable. */
export function validateFile(file: { name: string; size: number }, config: AppConfig): string | null {
  const allowed = normaliseExtensions(config.allowed_extensions);
  const ext = fileExtension(file.name);
  if (!ext || !allowed.includes(ext)) {
    const reason = ext
      ? `“${ext}” files aren't supported.`
      : "This file has no extension, so its format can't be checked.";
    return `${reason} Use one of: ${allowed.join(", ")}.`;
  }
  if (file.size === 0) {
    return "This file is empty. Choose a recording with audio in it.";
  }
  const maxBytes = config.max_file_mb * 1024 * 1024;
  if (file.size > maxBytes) {
    return `This file is ${formatBytes(file.size)}; the limit is ${config.max_file_mb} MB.`;
  }
  return null;
}
