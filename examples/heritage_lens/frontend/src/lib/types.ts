export type Provenance = "demo" | "live_bodhan" | "user_authored" | "illustrative";
export type Privacy = "private" | "public";
export type PublicationStatus = "draft" | "published" | "archived";

export const CATEGORIES: { value: string; label: string }[] = [
  { value: "oral_history", label: "Oral Histories" },
  { value: "festival", label: "Festivals" },
  { value: "traditional_art", label: "Traditional Art" },
  { value: "historical_document", label: "Historical Documents" },
  { value: "architecture", label: "Architecture" },
  { value: "traditional_music", label: "Traditional Music" },
  { value: "dance", label: "Dance" },
  { value: "food_recipes", label: "Food and Recipes" },
  { value: "indigenous_knowledge", label: "Indigenous Knowledge" },
  { value: "personal_memory", label: "Personal Memories" },
];
export const categoryLabel = (v?: string | null) => CATEGORIES.find((c) => c.value === v)?.label ?? "Uncategorised";

export interface User {
  id: number; username: string; email: string; display_name: string | null; bio: string | null;
  preferred_language: string | null; created_at: string;
}
export interface TokenResponse { access_token: string; token_type: string; expires_in: number; user: User }

export interface Story {
  id: number | string; user_id?: number; title: string; description: string | null; country: string | null;
  region: string | null; city: string | null; village: string | null; language: string | null;
  category: string | null; historical_period: string | null; event_year: number | null;
  event_year_approximate: boolean; original_text: string | null; tags: string[];
  latitude: number | null; longitude: number | null; privacy: Privacy; publication_status: PublicationStatus;
  consent_confirmed: boolean; consent_at: string | null; attribution?: string | null;
  created_at: string; updated_at: string; provenance: Provenance; cover_image_id?: number | null;
}
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }

export interface Segment { index: number; source_text: string; result_text: string; truncated: boolean }
export interface Translation {
  id: number; story_id: number; source_language: string | null; target_language: string; script: string;
  original_text: string; translated_text: string; edited_text: string | null; truncated: boolean;
  provider: string; created_at: string; updated_at?: string; provenance: Provenance;
}
export interface Transliteration {
  id: number; story_id: number | null; language: string; source_text: string; target_script: string;
  converted_text: string; truncated: boolean; provider: string; created_at: string; provenance: Provenance;
}
export interface Audio {
  id: number; story_id: number | null; kind: "original" | "generated"; original_filename: string | null;
  mime_type: string | null; size_bytes: number | null; language: string | null; duration: number | null;
  transcript: string | null; transcript_edited: string | null; transcript_language: string | null;
  transcript_segments: { index: number; start_seconds: number; end_seconds: number; text: string }[] | null;
  narrated_text: string | null; narration_voice: string | null; narration_style: string | null;
  translation_id: number | null; has_original_audio: boolean; has_generated_audio: boolean;
  created_at: string; provenance: Provenance; transcript_provenance: Provenance | null;
}
export interface OcrPage { page_number: number; markdown: string; blocks: unknown[]; error: string | null; attempts: number }
export interface DocumentRec {
  id: number; story_id: number | null; original_filename: string; mime_type: string; size_bytes: number;
  page_count: number | null; document_type: string; description: string | null; extracted_text: string | null;
  extracted_edited_text: string | null; ocr_pages: OcrPage[] | null;
  ocr_status: "not_run" | "completed" | "partial" | "failed"; ocr_error: string | null; created_at: string;
  ocr_provenance: Provenance | null;
}
export interface ImageRec {
  id: number; story_id: number | null; original_filename: string | null; caption: string | null;
  attribution: string | null; created_at: string;
}
export interface Capsule {
  id: number; title: string; message: string | null; dedication: string | null;
  intended_release_date: string | null; privacy: Privacy; status: "draft" | "sealed"; has_audio: boolean;
  created_at: string; updated_at: string; delivery_note: string; provenance: Provenance;
}
export interface Bundle {
  story: Story; contributor: string; is_owner: boolean; translations: Translation[];
  transliterations: Transliteration[]; audio: Audio[]; images: ImageRec[]; documents: DocumentRec[];
}
export interface Facet { value: string; count: number }
export interface Facets { countries: Facet[]; regions: Facet[]; languages: Facet[]; categories: Facet[]; periods: Facet[]; total: number }
export interface MapItem {
  id: number | string; title: string; description: string | null; country: string | null; region: string | null;
  city: string | null; village: string | null; language: string | null; category: string | null;
  latitude: number; longitude: number; cover_image_id: number | null; provenance: Provenance; note?: string;
}
export interface ConfigStatus {
  environment: string;
  services: Record<string, { configured: boolean; key_source: "service" | "shared" | null }>;
  all_configured: boolean; none_configured: boolean; ffmpeg: { available: boolean }; pdf_support: boolean;
}
export interface Capabilities {
  ffmpeg_available: boolean | null;
  services: {
    "indic-transcribe": { languages: Record<string, string>; max_seconds_per_request: number };
    "indic-translate": { target_languages: Record<string, string>; unsupported_languages: Record<string, string>; scripts: string[]; codemix_unavailable_for: string[] };
    "indic-transliterate": { languages: Record<string, string>; scripts: string[] };
    "indic-speak": { languages: Record<string, string>; voices: Record<string, { gender: string; recorded_in: string }>; styles: string[] };
    "indic-ocr": { upstream_formats: string[]; table_formats: string[] };
  };
}
export interface Dashboard {
  stories: { total: number; drafts: number; published: number; public: number; private: number };
  by_category: { category: string; count: number }[];
  documents: { total: number; digitized: number };
  audio: { original: number; generated: number; original_seconds: number };
  translations: number; transliterations: number; saved: number;
  time_capsules: { total: number; sealed: number };
  recent_activity: { type: string; id: number; label: string; at: string }[];
}
export interface TranslateResult { provenance: Provenance; translation: string; target_language: string; script: string; truncated: boolean; segments: Segment[]; saved: Translation | null }
export interface TransliterateResult { provenance: Provenance; converted_text: string; language: string; script: string; truncated: boolean; segments: Segment[]; saved: Transliteration | null }
export interface BodhanServiceHealth {
  service: string;
  configured: boolean;
  last_verified_at: string | null;
  last_result: "pass" | "fail" | "untested";
  status: "operational" | "error" | "not_configured" | "unknown";
  error_category: string | null;
  diagnostic_detail: string | null;
  http_status?: number | null;
  response_type?: string | null;
  response_time_ms?: number | null;
}
