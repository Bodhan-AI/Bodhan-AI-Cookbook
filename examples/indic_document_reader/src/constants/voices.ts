// ─────────────────────────────────────────────────────────────
// TTS Voice catalog — 45 voices across 22 languages
// Any voice can speak any language.
// ─────────────────────────────────────────────────────────────

export interface Voice {
  name: string;
  gender: 'female' | 'male';
  /** Language the voice was originally recorded in */
  originLanguage: string;
  originLanguageCode: string;
}

export const VOICES: Voice[] = [
  // Assamese
  { name: 'Prastuti',  gender: 'female', originLanguage: 'Assamese',              originLanguageCode: 'as' },
  { name: 'Ankur',     gender: 'male',   originLanguage: 'Assamese',              originLanguageCode: 'as' },
  // Bengali
  { name: 'Ishita',    gender: 'female', originLanguage: 'Bengali',               originLanguageCode: 'bn' },
  { name: 'Sourav',    gender: 'male',   originLanguage: 'Bengali',               originLanguageCode: 'bn' },
  // Bodo
  { name: 'Gwrbw',     gender: 'female', originLanguage: 'Bodo',                  originLanguageCode: 'brx' },
  { name: 'Sansuma',   gender: 'male',   originLanguage: 'Bodo',                  originLanguageCode: 'brx' },
  // Dogri
  { name: 'Preeti',    gender: 'female', originLanguage: 'Dogri',                 originLanguageCode: 'doi' },
  { name: 'Sham',      gender: 'male',   originLanguage: 'Dogri',                 originLanguageCode: 'doi' },
  // Gujarati
  { name: 'Dhara',     gender: 'female', originLanguage: 'Gujarati',              originLanguageCode: 'gu' },
  { name: 'Parth',     gender: 'male',   originLanguage: 'Gujarati',              originLanguageCode: 'gu' },
  // Hindi
  { name: 'Kavya',     gender: 'female', originLanguage: 'Hindi',                 originLanguageCode: 'hi' },
  { name: 'Suhani',    gender: 'female', originLanguage: 'Hindi',                 originLanguageCode: 'hi' },
  { name: 'Amit',      gender: 'male',   originLanguage: 'Hindi',                 originLanguageCode: 'hi' },
  // Kannada
  { name: 'Deepika',   gender: 'female', originLanguage: 'Kannada',               originLanguageCode: 'kn' },
  { name: 'Adarsh',    gender: 'male',   originLanguage: 'Kannada',               originLanguageCode: 'kn' },
  // Konkani
  { name: 'Anjali',    gender: 'female', originLanguage: 'Konkani',               originLanguageCode: 'kok' },
  { name: 'Sandeep',   gender: 'male',   originLanguage: 'Konkani',               originLanguageCode: 'kok' },
  // Kashmiri
  { name: 'Zoon',      gender: 'female', originLanguage: 'Kashmiri',              originLanguageCode: 'ks' },
  { name: 'Ishfaq',    gender: 'male',   originLanguage: 'Kashmiri',              originLanguageCode: 'ks' },
  // Maithili
  { name: 'Vaidehi',   gender: 'female', originLanguage: 'Maithili',              originLanguageCode: 'mai' },
  { name: 'Madhukar',  gender: 'male',   originLanguage: 'Maithili',              originLanguageCode: 'mai' },
  // Malayalam
  { name: 'Lakshmi',   gender: 'female', originLanguage: 'Malayalam',             originLanguageCode: 'ml' },
  { name: 'Kiran',     gender: 'male',   originLanguage: 'Malayalam',             originLanguageCode: 'ml' },
  // Manipuri
  { name: 'Thoibi',    gender: 'female', originLanguage: 'Manipuri',              originLanguageCode: 'mni' },
  { name: 'Chaoba',    gender: 'male',   originLanguage: 'Manipuri',              originLanguageCode: 'mni' },
  // Marathi
  { name: 'Anagha',    gender: 'female', originLanguage: 'Marathi',               originLanguageCode: 'mr' },
  { name: 'Chinmay',   gender: 'male',   originLanguage: 'Marathi',               originLanguageCode: 'mr' },
  // Nepali
  { name: 'Srijana',   gender: 'female', originLanguage: 'Nepali',                originLanguageCode: 'ne' },
  { name: 'Sagar',     gender: 'male',   originLanguage: 'Nepali',                originLanguageCode: 'ne' },
  // Odia
  { name: 'Itishree',  gender: 'female', originLanguage: 'Odia',                  originLanguageCode: 'or' },
  { name: 'Akash',     gender: 'male',   originLanguage: 'Odia',                  originLanguageCode: 'or' },
  // Punjabi
  { name: 'Kaur',      gender: 'female', originLanguage: 'Punjabi',               originLanguageCode: 'pa' },
  { name: 'Manpreet',  gender: 'male',   originLanguage: 'Punjabi',               originLanguageCode: 'pa' },
  // Sanskrit
  { name: 'Bharati',   gender: 'female', originLanguage: 'Sanskrit',              originLanguageCode: 'sa' },
  { name: 'Aryaman',   gender: 'male',   originLanguage: 'Sanskrit',              originLanguageCode: 'sa' },
  // Santali
  { name: 'Phulmani',  gender: 'female', originLanguage: 'Santali',               originLanguageCode: 'sat' },
  { name: 'Sibu',      gender: 'male',   originLanguage: 'Santali',               originLanguageCode: 'sat' },
  // Sindhi
  { name: 'Moomal',    gender: 'female', originLanguage: 'Sindhi',                originLanguageCode: 'sd' },
  { name: 'Rano',      gender: 'male',   originLanguage: 'Sindhi',                originLanguageCode: 'sd' },
  // Tamil
  { name: 'Anitha',    gender: 'female', originLanguage: 'Tamil',                 originLanguageCode: 'ta' },
  { name: 'Arun',      gender: 'male',   originLanguage: 'Tamil',                 originLanguageCode: 'ta' },
  // Telugu
  { name: 'Sravani',   gender: 'female', originLanguage: 'Telugu',                originLanguageCode: 'te' },
  { name: 'Vamsi',     gender: 'male',   originLanguage: 'Telugu',                originLanguageCode: 'te' },
  // Urdu
  { name: 'Saba',      gender: 'female', originLanguage: 'Urdu',                  originLanguageCode: 'ur' },
  { name: 'Zaid',      gender: 'male',   originLanguage: 'Urdu',                  originLanguageCode: 'ur' },
];

/** Get voices for a specific origin language */
export function getVoicesForLanguage(langCode: string): Voice[] {
  return VOICES.filter(v => v.originLanguageCode === langCode);
}

/** Get the default voice for a language (first female voice, or first available) */
export function getDefaultVoice(langCode: string): Voice {
  const langVoices = getVoicesForLanguage(langCode);
  if (langVoices.length > 0) {
    return langVoices.find(v => v.gender === 'female') ?? langVoices[0];
  }
  // Fallback: Kavya (Hindi female)
  return VOICES.find(v => v.name === 'Kavya')!;
}

/** Get all female voices */
export function getFemaleVoices(): Voice[] {
  return VOICES.filter(v => v.gender === 'female');
}

/** Get all male voices */
export function getMaleVoices(): Voice[] {
  return VOICES.filter(v => v.gender === 'male');
}

