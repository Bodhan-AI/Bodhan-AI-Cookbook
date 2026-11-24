// ─────────────────────────────────────────────────────────────
// Language definitions for all Bodhan AI supported languages
// ─────────────────────────────────────────────────────────────

export interface Language {
  code: string;
  name: string;
  nativeName: string;
  script: string;
  /** Supported by Translation API */
  supportsTranslation: boolean;
  /** Supported by Transliteration API */
  supportsTransliteration: boolean;
  /** Supported by STT API */
  supportsSTT: boolean;
  /** Supported by TTS API */
  supportsTTS: boolean;
  /** Supports codemix script */
  supportsCodemix: boolean;
}

export const LANGUAGES: Language[] = [
  { code: 'as',       name: 'Assamese',               nativeName: 'অসমীয়া',           script: 'Bengali',        supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'bn',       name: 'Bengali',                nativeName: 'বাংলা',             script: 'Bengali',        supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'brx',      name: 'Bodo',                   nativeName: 'बड़ो',               script: 'Devanagari',     supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: false, supportsTTS: true,  supportsCodemix: true },
  { code: 'doi',      name: 'Dogri',                  nativeName: 'डोगरी',              script: 'Devanagari',     supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'en',       name: 'English',                nativeName: 'English',           script: 'Latin',          supportsTranslation: true,  supportsTransliteration: false, supportsSTT: true,  supportsTTS: true,  supportsCodemix: false },
  { code: 'gu',       name: 'Gujarati',               nativeName: 'ગુજરાતી',            script: 'Gujarati',       supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'hi',       name: 'Hindi',                  nativeName: 'हिन्दी',              script: 'Devanagari',     supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'kn',       name: 'Kannada',                nativeName: 'ಕನ್ನಡ',              script: 'Kannada',        supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'kok',      name: 'Konkani',                nativeName: 'कोंकणी',             script: 'Devanagari',     supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'ks',       name: 'Kashmiri (Perso-Arabic)', nativeName: 'كٲشُر',             script: 'Perso-Arabic',   supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'ks-Deva',  name: 'Kashmiri (Devanagari)',  nativeName: 'कॉशुर',             script: 'Devanagari',     supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: false, supportsTTS: false, supportsCodemix: false },
  { code: 'mai',      name: 'Maithili',               nativeName: 'मैथिली',             script: 'Devanagari',     supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'ml',       name: 'Malayalam',              nativeName: 'മലയാളം',            script: 'Malayalam',      supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'mni',      name: 'Manipuri (Meitei)',      nativeName: 'ꯃꯤꯇꯩꯂꯣꯟ',           script: 'Meitei Mayek',   supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'mni-Beng', name: 'Manipuri (Bengali)',     nativeName: 'মণিপুরী',            script: 'Bengali',        supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: false, supportsTTS: false, supportsCodemix: true },
  { code: 'mr',       name: 'Marathi',                nativeName: 'मराठी',              script: 'Devanagari',     supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'ne',       name: 'Nepali',                 nativeName: 'नेपाली',             script: 'Devanagari',     supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'or',       name: 'Odia',                   nativeName: 'ଓଡ଼ିଆ',              script: 'Odia',           supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'pa',       name: 'Punjabi',                nativeName: 'ਪੰਜਾਬੀ',             script: 'Gurmukhi',       supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'sa',       name: 'Sanskrit',               nativeName: 'संस्कृतम्',           script: 'Devanagari',     supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'sat',      name: 'Santali',                nativeName: 'ᱥᱟᱱᱛᱟᱲᱤ',            script: 'Ol Chiki',       supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'sd',       name: 'Sindhi (Devanagari)',    nativeName: 'सिन्धी',             script: 'Devanagari',     supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: false },
  { code: 'sd-Arab',  name: 'Sindhi (Perso-Arabic)',  nativeName: 'سنڌي',              script: 'Perso-Arabic',   supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: false, supportsTTS: false, supportsCodemix: false },
  { code: 'ta',       name: 'Tamil',                  nativeName: 'தமிழ்',              script: 'Tamil',          supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'te',       name: 'Telugu',                 nativeName: 'తెలుగు',             script: 'Telugu',         supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
  { code: 'ur',       name: 'Urdu',                   nativeName: 'اُردُو',              script: 'Perso-Arabic',   supportsTranslation: true,  supportsTransliteration: true,  supportsSTT: true,  supportsTTS: true,  supportsCodemix: true },
];

// STT-only languages (not available in Translation/Transliteration)
export const STT_ONLY_LANGUAGES = [
  { code: 'bhb', name: 'Bhili',    nativeName: 'भीली' },
  { code: 'bho', name: 'Bhojpuri', nativeName: 'भोजपुरी' },
];

/** Languages available as translation targets (excludes bhb, bho) */
export const TRANSLATION_LANGUAGES = LANGUAGES.filter(l => l.supportsTranslation);

/** Languages available for TTS */
export const TTS_LANGUAGES = LANGUAGES.filter(l => l.supportsTTS);

/** Languages available for transliteration (excludes en) */
export const TRANSLITERATION_LANGUAGES = LANGUAGES.filter(l => l.supportsTransliteration);

/** All languages available for STT (includes bhb, bho) */
export const STT_LANGUAGES = [
  ...LANGUAGES.filter(l => l.supportsSTT),
  ...STT_ONLY_LANGUAGES.map(l => ({ ...l, script: 'Devanagari', supportsTranslation: false, supportsTransliteration: false, supportsSTT: true, supportsTTS: false, supportsCodemix: false })),
];

/** Look up a language by code */
export function getLanguage(code: string): Language | undefined {
  return LANGUAGES.find(l => l.code === code);
}

/** Get display name for a language code */
export function getLanguageName(code: string): string {
  return getLanguage(code)?.name ?? code;
}

/** Get native name for a language code */
export function getNativeName(code: string): string {
  return getLanguage(code)?.nativeName ?? code;
}

export type ScriptMode = 'native' | 'roman' | 'codemix';

export type TTSStyle =
  | 'AIR style news'
  | 'Customer Care'
  | 'TV style news'
  | 'advertisements'
  | 'anger'
  | "children's stories"
  | 'disgust'
  | 'educational lecture'
  | 'fear'
  | 'happy'
  | 'news'
  | 'sad'
  | 'single person narration audiobook'
  | 'surprise';

export const TTS_STYLES: { value: TTSStyle | 'neutral'; label: string; icon: string }[] = [
  { value: 'neutral',                           label: 'Neutral',          icon: '🎙️' },
  { value: 'single person narration audiobook',  label: 'Audiobook',        icon: '📖' },
  { value: "children's stories",                 label: "Children's Story", icon: '🧸' },
  { value: 'educational lecture',                label: 'Educational',      icon: '🎓' },
  { value: 'news',                               label: 'News',             icon: '📰' },
  { value: 'TV style news',                      label: 'TV News',          icon: '📺' },
  { value: 'AIR style news',                     label: 'AIR News',         icon: '📻' },
  { value: 'Customer Care',                      label: 'Customer Care',    icon: '📞' },
  { value: 'advertisements',                     label: 'Advertisement',    icon: '📢' },
  { value: 'happy',                              label: 'Happy',            icon: '😊' },
  { value: 'sad',                                label: 'Sad',              icon: '😢' },
  { value: 'anger',                              label: 'Angry',            icon: '😠' },
  { value: 'fear',                               label: 'Fear',             icon: '😨' },
  { value: 'surprise',                           label: 'Surprise',         icon: '😲' },
  { value: 'disgust',                            label: 'Disgust',          icon: '🤢' },
];

