import type { ReactElement } from "react";

export type TranscriptionLanguage = "as" | "bhb" | "bho" | "bn" | "brx" | "doi" | "en" | "gu" | "hi" | "kn" | "kok" | "ks" | "mai" | "ml" | "mni" | "mr" | "ne" | "or" | "pa" | "sa" | "sat" | "sd" | "ta" | "te" | "ur";
export type TranslationLanguage = "as" | "bn" | "brx" | "doi" | "en" | "gu" | "hi" | "kn" | "ks-Deva" | "ks" | "kok" | "mai" | "ml" | "mni-Beng" | "mni" | "mr" | "ne" | "or" | "pa" | "sa" | "sat" | "sd" | "sd-Arab" | "ta" | "te" | "ur";
/** Backward-compatible alias for the spoken-language type. */
export type Language = TranscriptionLanguage;
export interface LanguageOption<T extends string> { readonly code: T; readonly label: string; }
export declare const TRANSCRIPTION_LANGUAGES: readonly LanguageOption<TranscriptionLanguage>[];
export declare const TRANSLATION_LANGUAGES: readonly LanguageOption<TranslationLanguage>[];
export declare function isTranscriptionLanguage(code: unknown): code is TranscriptionLanguage;
export declare function isTranslationLanguage(code: unknown): code is TranslationLanguage;

export interface CompactVoiceInputProps {
  /** Host backend prefix. Defaults to /api/bhasha. No provider credentials. */
  apiBaseUrl?: string;
  /** Initial spoken language. Defaults to en. */
  defaultLanguage?: Language;
  /** Insert into the host editor. Reject to keep the transcript for retry. */
  onInsert?: (text: string) => void | Promise<void>;
}

export interface SelectionTranslatorProps {
  /** Initial translation target. Defaults to en; no source-language guessing. */
  defaultTargetLanguage?: TranslationLanguage;
  /** Host backend prefix. Defaults to /api/bhasha. Mount once per document. */
  apiBaseUrl?: string;
  /** Show Copy original and Copy translation actions. Defaults to true. */
  showCopyButtons?: boolean;
}

export declare function CompactVoiceInput(props: CompactVoiceInputProps): ReactElement;
export declare function SelectionTranslator(props: SelectionTranslatorProps): ReactElement | null;
