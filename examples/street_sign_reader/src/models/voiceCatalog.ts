// File: src/models/voiceCatalog.ts
import { LanguageVoiceCatalogItem } from './SignboardTypes';

export const BODHAN_LANGUAGES: LanguageVoiceCatalogItem[] = [
  // Southern
  {
    code: 'ta',
    name: 'Tamil',
    nativeName: 'தமிழ்',
    zone: 'Southern',
    defaultVoice: 'Anitha',
    voices: [
      { name: 'Anitha', gender: 'Female' },
      { name: 'Arun', gender: 'Male' }
    ]
  },
  {
    code: 'te',
    name: 'Telugu',
    nativeName: 'తెలుగు',
    zone: 'Southern',
    defaultVoice: 'Sravani',
    voices: [
      { name: 'Sravani', gender: 'Female' },
      { name: 'Vamsi', gender: 'Male' }
    ]
  },
  {
    code: 'kn',
    name: 'Kannada',
    nativeName: 'ಕನ್ನಡ',
    zone: 'Southern',
    defaultVoice: 'Deepika',
    voices: [
      { name: 'Deepika', gender: 'Female' },
      { name: 'Adarsh', gender: 'Male' }
    ]
  },
  {
    code: 'ml',
    name: 'Malayalam',
    nativeName: 'മലയാളം',
    zone: 'Southern',
    defaultVoice: 'Lakshmi',
    voices: [
      { name: 'Lakshmi', gender: 'Female' },
      { name: 'Kiran', gender: 'Male' }
    ]
  },

  // Western & Central
  {
    code: 'hi',
    name: 'Hindi',
    nativeName: 'हिन्दी',
    zone: 'Western & Central',
    defaultVoice: 'Kavya',
    voices: [
      { name: 'Kavya', gender: 'Female' },
      { name: 'Suhani', gender: 'Female' },
      { name: 'Amit', gender: 'Male' }
    ]
  },
  {
    code: 'gu',
    name: 'Gujarati',
    nativeName: 'ગુજરાતી',
    zone: 'Western & Central',
    defaultVoice: 'Dhara',
    voices: [
      { name: 'Dhara', gender: 'Female' },
      { name: 'Parth', gender: 'Male' }
    ]
  },
  {
    code: 'mr',
    name: 'Marathi',
    nativeName: 'मराठी',
    zone: 'Western & Central',
    defaultVoice: 'Anagha',
    voices: [
      { name: 'Anagha', gender: 'Female' },
      { name: 'Chinmay', gender: 'Male' }
    ]
  },
  {
    code: 'kok',
    name: 'Konkani',
    nativeName: 'कोंकणी',
    zone: 'Western & Central',
    defaultVoice: 'Anjali',
    voices: [
      { name: 'Anjali', gender: 'Female' },
      { name: 'Sandeep', gender: 'Male' }
    ]
  },
  {
    code: 'sd',
    name: 'Sindhi',
    nativeName: 'سنڌي / सिंधी',
    zone: 'Western & Central',
    defaultVoice: 'Moomal',
    voices: [
      { name: 'Moomal', gender: 'Female' },
      { name: 'Rano', gender: 'Male' }
    ]
  },

  // Eastern
  {
    code: 'bn',
    name: 'Bengali',
    nativeName: 'বাংলা',
    zone: 'Eastern',
    defaultVoice: 'Ishita',
    voices: [
      { name: 'Ishita', gender: 'Female' },
      { name: 'Sourav', gender: 'Male' }
    ]
  },
  {
    code: 'or',
    name: 'Odia',
    nativeName: 'ଓଡ଼ିଆ',
    zone: 'Eastern',
    defaultVoice: 'Itishree',
    voices: [
      { name: 'Itishree', gender: 'Female' },
      { name: 'Akash', gender: 'Male' }
    ]
  },
  {
    code: 'as',
    name: 'Assamese',
    nativeName: 'অসমীয়া',
    zone: 'Eastern',
    defaultVoice: 'Prastuti',
    voices: [
      { name: 'Prastuti', gender: 'Female' },
      { name: 'Ankur', gender: 'Male' }
    ]
  },
  {
    code: 'mai',
    name: 'Maithili',
    nativeName: 'मैथिली',
    zone: 'Eastern',
    defaultVoice: 'Vaidehi',
    voices: [
      { name: 'Vaidehi', gender: 'Female' },
      { name: 'Madhukar', gender: 'Male' }
    ]
  },
  {
    code: 'sat',
    name: 'Santali',
    nativeName: 'ᱥᱟᱱᱛᱟᱲᱤ',
    zone: 'Eastern',
    defaultVoice: 'Phulmani',
    voices: [
      { name: 'Phulmani', gender: 'Female' },
      { name: 'Sibu', gender: 'Male' }
    ]
  },
  {
    code: 'mni',
    name: 'Manipuri',
    nativeName: 'ꯃꯤꯇꯩꯂꯣꯟ',
    zone: 'Eastern',
    defaultVoice: 'Thoibi',
    voices: [
      { name: 'Thoibi', gender: 'Female' },
      { name: 'Chaoba', gender: 'Male' }
    ]
  },

  // Northern
  {
    code: 'pa',
    name: 'Punjabi',
    nativeName: 'ਪੰਜਾਬੀ',
    zone: 'Northern',
    defaultVoice: 'Kaur',
    voices: [
      { name: 'Kaur', gender: 'Female' },
      { name: 'Manpreet', gender: 'Male' }
    ]
  },
  {
    code: 'ur',
    name: 'Urdu',
    nativeName: 'اردو',
    zone: 'Northern',
    defaultVoice: 'Saba',
    voices: [
      { name: 'Saba', gender: 'Female' },
      { name: 'Zaid', gender: 'Male' }
    ]
  },
  {
    code: 'ks',
    name: 'Kashmiri',
    nativeName: 'کٲشُر',
    zone: 'Northern',
    defaultVoice: 'Zoon',
    voices: [
      { name: 'Zoon', gender: 'Female' },
      { name: 'Ishfaq', gender: 'Male' }
    ]
  },
  {
    code: 'ne',
    name: 'Nepali',
    nativeName: 'नेपाली',
    zone: 'Northern',
    defaultVoice: 'Srijana',
    voices: [
      { name: 'Srijana', gender: 'Female' },
      { name: 'Sagar', gender: 'Male' }
    ]
  },
  {
    code: 'doi',
    name: 'Dogri',
    nativeName: 'डोगरी',
    zone: 'Northern',
    defaultVoice: 'Preeti',
    voices: [
      { name: 'Preeti', gender: 'Female' },
      { name: 'Sham', gender: 'Male' }
    ]
  },
  {
    code: 'brx',
    name: 'Bodo',
    nativeName: 'बड़ो',
    zone: 'Northern',
    defaultVoice: 'Gwrbw',
    voices: [
      { name: 'Gwrbw', gender: 'Female' },
      { name: 'Sansuma', gender: 'Male' }
    ]
  },
  {
    code: 'sa',
    name: 'Sanskrit',
    nativeName: 'संस्कृतम्',
    zone: 'Northern',
    defaultVoice: 'Bharati',
    voices: [
      { name: 'Bharati', gender: 'Female' },
      { name: 'Aryaman', gender: 'Male' }
    ]
  },

  // Global
  {
    code: 'en',
    name: 'English',
    nativeName: 'English (India)',
    zone: 'Global',
    defaultVoice: 'Arun',
    voices: [
      { name: 'Kavya', gender: 'Female' },
      { name: 'Arun', gender: 'Male' }
    ]
  }
];

export function getLanguageByCode(code: string): LanguageVoiceCatalogItem {
  return BODHAN_LANGUAGES.find((l) => l.code === code) || BODHAN_LANGUAGES[4]; // Default to Hindi
}
