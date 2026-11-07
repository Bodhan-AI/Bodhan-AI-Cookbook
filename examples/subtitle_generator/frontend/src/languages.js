// Spoken languages offered for indic-transcribe, as [ISO code, label].
export const LANGUAGES = [
  ['hi', 'Hindi (हिंदी)'],
  ['en', 'English'],
  ['ta', 'Tamil (தமிழ்)'],
  ['te', 'Telugu (తెలుగు)'],
  ['ml', 'Malayalam (മലയാളം)'],
  ['bn', 'Bengali (বাংলা)'],
  ['mr', 'Marathi (मराठी)'],
  ['gu', 'Gujarati (ગુજરાતી)'],
  ['kn', 'Kannada (ಕನ್ನಡ)'],
  ['pa', 'Punjabi (ਪੰਜਾਬੀ)'],
  ['or', 'Odia (ଓଡ଼ିଆ)'],
  ['ur', 'Urdu (اردو)'],
  ['as', 'Assamese (অসমীয়া)'],
  ['ne', 'Nepali (नेपाली)'],
  ['sa', 'Sanskrit (संस्कृतम्)'],
  ['bho', 'Bhojpuri (भोजपुरी)'],
  ['mai', 'Maithili (मैथिली)'],
]

// indic-translate does not support Bhojpuri as an output language.
export const TRANSLATION_LANGUAGES = LANGUAGES.filter(([code]) => code !== 'bho')

export function languageName(code) {
  return LANGUAGES.find(([c]) => c === code)?.[1] ?? code
}
