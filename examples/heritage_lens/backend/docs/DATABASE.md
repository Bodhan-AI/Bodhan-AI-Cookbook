# Database

SQLite (dev, foreign keys enabled by PRAGMA) with a PostgreSQL-compatible schema: portable VARCHAR enums,
generic JSON, timezone-aware UTC timestamps (`UTCDateTime`).

| Table | Purpose / notable columns |
|---|---|
| users | username, email (unique, lowercase), bcrypt `password_hash`, profile |
| heritage_stories | contributor's own text (`original_text`), location (+ optional lat/lon, range-checked, never guessed), `privacy`, `publication_status`, `consent_confirmed/consent_at`, `attribution`, tags |
| audio_recordings | `kind` original/generated; `original_audio_path` or `generated_audio_path` (CHECK: one required); machine `transcript` vs `transcript_edited`; narration text/voice/style; `translation_id` |
| translations | `original_text` snapshot, `translated_text` (machine), `edited_text`, `truncated`, `segments` JSON |
| transliterations | `language`, `source_text`, `target_script`, `converted_text` (no source-script column: the API has none) |
| historical_documents | untouched `stored_path`; `extracted_text` (raw OCR), `extracted_edited_text`, `ocr_pages` JSON, `ocr_status` |
| heritage_images | image path, caption, attribution |
| time_capsules | title, message, dedication, `intended_release_date`, privacy, status (model only; no routes yet) |

Cascade rules: deleting a user removes everything they own. Deleting a story removes its translations and
transliterations and its generated narration; original recordings, scans and photographs are **detached**
(`story_id` -> NULL) unless `DELETE /api/stories/{id}?delete_media=true`.
Indexes: user/story foreign keys, language, category, country, region, created_at, public-listing composite.
