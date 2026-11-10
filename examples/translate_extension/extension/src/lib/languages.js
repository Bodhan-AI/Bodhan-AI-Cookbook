// Target languages accepted by the Bodhan Translate API (26 codes, 23 languages).
// Kashmiri, Manipuri and Sindhi each come in two scripts; the bare code is the default script.
export const LANGUAGES = Object.freeze([
  { code: 'en', name: 'English', native: 'English' },
  { code: 'hi', name: 'Hindi', native: 'हिन्दी' },
  { code: 'bn', name: 'Bengali', native: 'বাংলা' },
  { code: 'ta', name: 'Tamil', native: 'தமிழ்' },
  { code: 'te', name: 'Telugu', native: 'తెలుగు' },
  { code: 'mr', name: 'Marathi', native: 'मराठी' },
  { code: 'gu', name: 'Gujarati', native: 'ગુજરાતી' },
  { code: 'kn', name: 'Kannada', native: 'ಕನ್ನಡ' },
  { code: 'ml', name: 'Malayalam', native: 'മലയാളം' },
  { code: 'pa', name: 'Punjabi', native: 'ਪੰਜਾਬੀ' },
  { code: 'or', name: 'Odia', native: 'ଓଡ଼ିଆ' },
  { code: 'as', name: 'Assamese', native: 'অসমীয়া' },
  { code: 'ur', name: 'Urdu', native: 'اردو' },
  { code: 'ne', name: 'Nepali', native: 'नेपाली' },
  { code: 'sa', name: 'Sanskrit', native: 'संस्कृतम्' },
  { code: 'kok', name: 'Konkani', native: 'कोंकणी' },
  { code: 'mai', name: 'Maithili', native: 'मैथिली' },
  { code: 'doi', name: 'Dogri', native: 'डोगरी' },
  { code: 'brx', name: 'Bodo', native: 'बड़ो' },
  { code: 'sat', name: 'Santali', native: 'ᱥᱟᱱᱛᱟᱲᱤ' },
  { code: 'ks', name: 'Kashmiri (Perso-Arabic)', native: 'کٲشُر' },
  { code: 'ks-Deva', name: 'Kashmiri (Devanagari)', native: 'कॉशुर' },
  { code: 'mni', name: 'Manipuri (Meitei)', native: 'ꯃꯤꯇꯩꯂꯣꯟ' },
  { code: 'mni-Beng', name: 'Manipuri (Bengali)', native: 'মৈতৈলোন্' },
  { code: 'sd', name: 'Sindhi (Devanagari)', native: 'सिन्धी' },
  { code: 'sd-Arab', name: 'Sindhi (Perso-Arabic)', native: 'سنڌي' },
]);

export const SCRIPTS = Object.freeze([
  { code: 'native', name: 'Native script' },
  { code: 'roman', name: 'Roman (Latin letters)' },
  { code: 'codemix', name: 'Code-mixed (keeps some English words)' },
]);

export function isLanguageCode(code) {
  return LANGUAGES.some((l) => l.code === code);
}

/**
 * The script to actually request for a target. English has no script choice, and
 * codemix isn't available for ks-Deva / sd-Arab, so those fall back to native.
 */
export function effectiveScript(target, script) {
  if (target === 'en') return 'native';
  if (script === 'codemix' && (target === 'ks-Deva' || target === 'sd-Arab')) return 'native';
  return SCRIPTS.some((s) => s.code === script) ? script : 'native';
}

// indic-speak voices by the language each was recorded in (female, male). Any voice
// can read any language, so a language without its own voices borrows Hindi's.
export const VOICES = Object.freeze({
  as: ['Prastuti', 'Ankur'],
  bn: ['Ishita', 'Sourav'],
  brx: ['Gwrbw', 'Sansuma'],
  doi: ['Preeti', 'Sham'],
  gu: ['Dhara', 'Parth'],
  hi: ['Kavya', 'Amit'],
  kn: ['Deepika', 'Adarsh'],
  kok: ['Anjali', 'Sandeep'],
  ks: ['Zoon', 'Ishfaq'],
  mai: ['Vaidehi', 'Madhukar'],
  ml: ['Lakshmi', 'Kiran'],
  mni: ['Thoibi', 'Chaoba'],
  mr: ['Anagha', 'Chinmay'],
  ne: ['Srijana', 'Sagar'],
  or: ['Itishree', 'Akash'],
  pa: ['Kaur', 'Manpreet'],
  sa: ['Bharati', 'Aryaman'],
  sat: ['Phulmani', 'Sibu'],
  sd: ['Moomal', 'Rano'],
  ta: ['Anitha', 'Arun'],
  te: ['Sravani', 'Vamsi'],
  ur: ['Saba', 'Zaid'],
});

/** indic-speak takes a language, not a script: "ks-Deva" is read as Kashmiri ("ks"). */
export function speechLang(target) {
  return String(target).split('-')[0];
}

/** gender is "female" | "male". */
export function pickVoice(lang, gender) {
  const pair = VOICES[lang] ?? VOICES.hi;
  return gender === 'male' ? pair[1] : pair[0];
}
