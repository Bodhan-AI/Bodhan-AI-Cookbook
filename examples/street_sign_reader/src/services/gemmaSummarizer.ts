// File: src/services/gemmaSummarizer.ts
import { AppSettings, SignboardDomain, ExtraUserContext } from '../models/SignboardTypes';
import { getLanguageByCode } from '../models/voiceCatalog';
import { sanitizeGibberishAndCodes } from './onDeviceSignCleaner';
import { performBodhanTranslate } from './bodhanClient';

// Major languages with high-resource corpora where LLMs are reliable and accurate:
export const GEMMA_SUPPORTED_LANGUAGES = [
  'en', 'hi', 'ta', 'te', 'bn', 'mr', 'gu', 'kn', 'ml', 'pa', 'ur'
];

/**
 * Returns true only for the major 11 languages where LLMs have solid multilingual corpora.
 * For languages with distinct/rare scripts or low-resource corpora (Santali, Manipuri,
 * Kashmiri, Sindhi, Bodo, Sanskrit, Maithili, Dogri, Konkani, Nepali, etc.),
 * this returns false so the app completely bypasses the LLM and executes 100% via
 * Bodhan's Sovereign Indic pipeline (Indic OCR -> IndicTrans2 -> Indic-Speak TTS).
 */
export function isLanguageSupportedByGemma(langCode: string): boolean {
  return GEMMA_SUPPORTED_LANGUAGES.includes(langCode);
}

export interface GemmaSummarizeInput {
  brandName: string;
  servicesText: string;
  directionGuide?: string;
  hoursText?: string;
  targetLanguage: string;
  rawBlocks?: string[];
  archetype?: 'STREET_WAYFINDING' | 'COMMERCIAL_STORE' | 'TRANSIT_REGULATORY';
}

export interface GemmaIdentificationResult {
  signCategory: string;
  archetype: 'COMMERCIAL_STORE' | 'STREET_WAYFINDING' | 'TRANSIT_REGULATORY';
  name: string;
  services: string[];
  servicesText: string;
  timings?: string;
  urgentNotice?: string;
  address?: string;
  phoneNumbers: string[];
  spokenSentence: string;
  keyFacts?: string[];
  actionableTip?: string;
}

export interface GemmaSummarizeResult {
  synthesizedScript: string;
  rawConcatenation: string;
  source: 'groq' | 'openrouter' | 'custom' | 'builtin';
  modelUsed: string;
  domain: SignboardDomain;
  extraContext: ExtraUserContext;
  identification?: GemmaIdentificationResult;
  processingTimeMs: number;
}

/**
 * Detect the signboard domain based on keywords in brand, services, raw text blocks, and archetype.
 */
export function detectSignboardDomain(
  brand: string,
  services: string,
  rawTexts: string[] = [],
  archetype?: 'STREET_WAYFINDING' | 'COMMERCIAL_STORE' | 'TRANSIT_REGULATORY'
): SignboardDomain {
  if (archetype === 'STREET_WAYFINDING') return 'ROAD_HIGHWAY';
  if (archetype === 'TRANSIT_REGULATORY') return 'RAILWAY_TRANSIT';
  if (archetype === 'COMMERCIAL_STORE') return 'COMMERCIAL';

  const combined = `${brand} ${services} ${rawTexts.join(' ')}`.toLowerCase();

  // 1. Railway / Transit Station
  const railwayRegex =
    /\b(platform|pf[\s-]?\d|railway|train|coach|fob|foot\s*overbridge|overbridge|waiting\s*hall|waiting\s*room|cloak\s*room|ticket\s*counter|ticket|exit\s*gate|entry\s*gate|enquiry|inquiry|station\s*master|irctc|प्लेटफॉर्म|रेलवे|गाड़ी|ପ୍ଲାଟଫର୍ମ|ரயில்|ప్లాట్‌ఫాం|ರೈಲ್ವೆ)\b/i;
  if (railwayRegex.test(combined)) {
    return 'RAILWAY_TRANSIT';
  }

  // 2. Road, Street & Highway Wayfinding
  const roadRegex =
    /\b(nh[\s-]?\d+|sh[\s-]?\d+|national\s*highway|state\s*highway|expressway|toll\s*plaza|toll|bypass|circle|chowk|junction|roundabout|speed\s*limit|keep\s*left|keep\s*right|lane|\b\d+\s*(?:km|kms|किमी|कि\.मी\.|मीटर|கி\.மீ)\b|way\s*to|towards|street|st\.?|road|rd\.?|marg|salai|lane|cross|avenue|gali|गली|स्ट्रीट|रोड|मार्ग|सालाई|सड़क|தெரு|சாலை|வீதி|రహదారి|ರಸ್ತೆ|ward|वार्ड|வட்டம்|மண்டலம்|zone|क्षेत्र|मंडल|division)\b/i;
  if (roadRegex.test(combined)) {
    return 'ROAD_HIGHWAY';
  }

  // 3. Civic & Public Utilities
  const civicRegex =
    /\b(hospital|dispensary|emergency|casualty|police\s*station|police\s*chowki|post\s*office|court|metro\s*station|parking|restroom|toilet|washroom|municipal|ward\s*office|अस्पताल|थाना|चिकित्सालय)\b/i;
  if (civicRegex.test(combined)) {
    return 'CIVIC_PUBLIC';
  }

  // 4. Default: Commercial Store, Clinic, Restaurant, Retail
  return 'COMMERCIAL';
}

// Common generic street, transit, and shop descriptor words in English
const GENERIC_DESCRIPTORS_REGEX =
  /\b(?:street|st|road|rd|marg|salai|lane|cross|avenue|ave|circle|chowk|nagar|colony|layout|extension|gali|bazaar|market|path|way|drive|square|store|shop|mart|centre|center|clinic|hospital|pharmacy|chemist|medical|enterprises|agency|emporium|ward|zone|division)\b/gi;

// Generic category words across Indian scripts
const INDIC_GENERIC_WORDS = [
  // Devanagari (Hindi, Marathi)
  'गली', 'गल्ली', 'स्ट्रीट', 'रोड', 'मार्ग', 'सालाई', 'सड़क', 'सडक', 'चौक', 'रास्ता', 'नगर', 'कॉलोनी', 'बाज़ार', 'बाजार', 'लेन', 'क्रॉस', 'एवेन्यू', 'दुकान', 'स्टोर', 'शॉप', 'क्लिनिक', 'केंद्र', 'अस्पताल', 'औषधालय', 'दवाखाना', 'वार्ड', 'क्षेत्र', 'मंडल',
  // Tamil
  'தெரு', 'சாலை', 'வீதி', 'நகர்', 'காலனி', 'வட்டம்', 'மண்டலம்', 'கடை', 'நிலையம்', 'மருந்தகம்', 'அங்காடி', 'வார்டு', 'பகுதி',
  // Telugu
  'వీధి', 'రహదారి', 'రోడ్డు', 'నగర్', 'కాలనీ', 'దుకాణం', 'స్టోర్', 'కేంద్రం', 'వార్డు',
  // Kannada
  'ರಸ್ತೆ', 'ಬೀದಿ', 'ಮಾರ್ಗ', 'ನಗರ', 'ಅಂಗಡಿ', 'ಕೇಂದ್ರ', 'ವಾರ್ಡ್',
  // Bengali
  'রাস্তা', 'সড়ক', 'লেন', 'নগর', 'দোকান', 'ওয়ার্ড',
  // Gujarati
  'ગલી', 'રસ્તો', 'માર્ગ', 'નગર', 'દુકાન', 'વોર્ડ',
  // Malayalam
  'റോഡ്', 'തെരുവ്', 'നഗർ', 'കട', 'വാർഡ്',
  // Odia
  'ଗଳି', 'ରାସ୍ତା', 'ମାର୍ଗ', 'ନଗର', 'ଦୋକାନ', 'ୱାର୍ଡ',
  // Punjabi
  'ਗਲੀ', 'ਸੜਕ', 'ਮਾਰਗ', 'ਨਗਰ', 'ਦੁਕਾਨ', 'ਵਾਰਡ'
];

/**
 * Extracts core proper noun roots by removing generic category descriptors and punctuation.
 * e.g., "गोपालकृष्णन गली" -> ["गोपालकृष्णन"]
 *       "Gopalakrishnan Street" -> ["gopalakrishnan"]
 */
export function extractCoreEntityRoots(text: string): string[] {
  if (!text) return [];
  let cleaned = text;

  // Remove Indic generic words
  for (const word of INDIC_GENERIC_WORDS) {
    const r = new RegExp(`(^|[\\s.,:;\\-_/|])${word}([\\s.,:;\\-_/|]|$)`, 'gu');
    cleaned = cleaned.replace(r, ' ');
  }

  // Remove English generic words and symbols
  cleaned = cleaned.replace(GENERIC_DESCRIPTORS_REGEX, ' ');
  cleaned = cleaned.replace(/[()[\]{}<>;~|•*#^!।.,:;\/\-]+/g, ' ');

  return cleaned
    .split(/\s+/)
    .map((t) => t.trim().toLowerCase().replace(/[\s\-_.:,।]/g, ''))
    .filter((t) => t.length >= 3 && !/^\d+$/.test(t)); // Keep proper noun roots >= 3 chars, discard pure numbers
}

/**
 * Deduplicate text so proper nouns, bilingual translations, or identical lines
 * are not repeated redundantly across scripts and synonyms.
 * Handles cases like:
 *   brand = "गोपालकृष्णन गली"
 *   services = "गोपालकृष्णन स्ट्रीट। वार्ड - 174 क्षेत्र - 40ए मंडल - 13"
 *   -> cleanServices = "वार्ड - 174 क्षेत्र - 40ए मंडल - 13"
 */
export function deduplicateSignboardContent(
  brand: string,
  services: string
): { cleanBrand: string; cleanServices: string } {
  let cleanBrand = (brand || '').trim();
  let cleanServices = (services || '').trim();

  if (!cleanBrand && !cleanServices) return { cleanBrand: '', cleanServices: '' };

  const brandRoots = extractCoreEntityRoots(cleanBrand);

  // If services starts with the brand name directly, strip the duplicate prefix
  if (cleanBrand && cleanServices) {
    const escapedBrand = cleanBrand.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const duplicatePrefixRegex = new RegExp(`^${escapedBrand}[\\s.:,|।\\-]+`, 'iu');
    if (duplicatePrefixRegex.test(cleanServices)) {
      cleanServices = cleanServices.replace(duplicatePrefixRegex, '').trim();
    }
  }

  // Split services by clauses or delimiters (period, newline, pipe, semicolon, bullet, danda)
  const clauses = cleanServices
    .split(/[•|;\n।.]+|,\s*(?=[A-Za-z\u0900-\u0D7F]{3,})/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);

  const seenRoots = new Set<string>(brandRoots);
  const seenExact = new Set<string>();
  if (cleanBrand) {
    seenExact.add(cleanBrand.toLowerCase().replace(/[\s\-_.:,।]/g, ''));
  }

  const uniqueClauses: string[] = [];

  for (const clause of clauses) {
    const normClause = clause.toLowerCase().replace(/[\s\-_.:,।]/g, '');
    if (!normClause) continue;

    // 1. Direct exact duplicate with brand or already seen clause
    if (seenExact.has(normClause)) continue;

    // 2. Extract core roots from clause
    const clauseRoots = extractCoreEntityRoots(clause);

    // If clause contains core roots and ALL of them match brand roots (e.g. "गोपालकृष्णन स्ट्रीट" matching brand "गोपालकृष्णन गली")
    const isBrandRootDuplicate =
      brandRoots.length > 0 &&
      clauseRoots.length > 0 &&
      clauseRoots.every((root) => brandRoots.includes(root));

    if (isBrandRootDuplicate) {
      // Discard this duplicate clause entirely!
      continue;
    }

    // 3. Check if the clause has a prefix that matches any brand root (e.g., "गोपालकृष्णन स्ट्रीट: वार्ड 174")
    let cleanedClause = clause;
    for (const root of brandRoots) {
      const prefixRegex = new RegExp(`^[^\\w\\u0900-\\u0D7F]*${root}[\\w\\u0900-\\u0D7F\\s]*?[.:,|।\\-]+`, 'iu');
      if (prefixRegex.test(cleanedClause)) {
        cleanedClause = cleanedClause.replace(prefixRegex, '').trim();
      }
    }

    const normCleaned = cleanedClause.toLowerCase().replace(/[\s\-_.:,।]/g, '');
    if (!normCleaned || seenExact.has(normCleaned)) continue;

    seenExact.add(normCleaned);
    clauseRoots.forEach((r) => seenRoots.add(r));
    uniqueClauses.push(cleanedClause);
  }

  return {
    cleanBrand,
    cleanServices: uniqueClauses.join('. ')
  };
}

/**
 * Extract extra user context with domain badges, key details, and guidance tips.
 */
export function extractExtraContext(
  domain: SignboardDomain,
  brand: string,
  services: string,
  direction: string,
  hours: string,
  targetLang: string
): ExtraUserContext {
  const combined = `${brand} ${services} ${hours}`.trim();
  const keyDetails: string[] = [];

  let domainLabel = '🛍️ Commercial Retail & Services';
  let domainIcon = 'store';
  let guidanceTip = 'Pedestrian Tip: Registered phone numbers are available for direct dialing.';

  // Localized guidance messages
  const guidanceMessages: Record<SignboardDomain, Record<string, string>> = {
    ROAD_HIGHWAY: {
      en: 'Navigation Tip: Stay in the designated lane and follow route markers for your upcoming exit or junction.',
      hi: 'नेविगेशन सलाह: आगामी निकास या मोड़ के लिए संकेतित लेन में रहें और गति सीमा का पालन करें।',
      ta: 'வழிகாட்டுதல் குறிப்பு: உங்களின் திருப்பம் அல்லது சந்திப்பிற்கு குறிக்கப்பட்ட பாதையிலேயே செல்லுங்கள்.',
      te: 'నావిగేషన్ సూచన: మీ తదుపరి మలుపు కోసం సూచించిన లేన్‌లోనే ప్రయాణించండి.',
      bn: 'দিকনির্দেশনা পরামর্শ: সামনের বাঁক বা জংশনের জন্য নির্দিষ্ট লেনে থাকুন।',
      mr: 'दिशादर्शन सूचना: पुढील वळणासाठी किंवा चौकासाठी सूचित केलेल्या लेनमध्ये राहा.',
      gu: 'દિશાનિર્દેશ ટીપ: આગળના વળાંક કે જંક્શન માટે દર્શાવેલ લેનમાં જ વાહન ચલાવો.',
      kn: 'ಮಾರ್ಗದರ್ಶನ ಸಲಹೆ: ಮುಂದಿನ ತಿರುವು ಅಥವಾ ಜಂಕ್ಷನ್‌ಗಾಗಿ ಸೂಚಿಸಲಾದ ಲೇನ್‌ನಲ್ಲಿಯೇ ಚಲಿಸಿ.',
      ml: 'നാവിഗേഷൻ ടിപ്പ്: വരാനിരിക്കുന്ന തിരിവിനായി നിർദ്ദേശിച്ച പാതയിൽ തന്നെ തുടരുക.',
      pa: 'ਨੇਵੀਗੇਸ਼ਨ ਸਲਾਹ: ਅਗਲੇ ਮੋੜ ਜਾਂ ਚੌਰਾਹੇ ਲਈ ਨਿਰਧਾਰਤ ਲੇਨ ਵਿੱਚ ਹੀ ਰਹੋ।',
      or: 'ଦିଗନିର୍ଦ୍ଦେଶ ପରାମର୍ଶ: ଆଗାମୀ ମୋଡ଼ ବା ଛକ ପାଇଁ ନିର୍ଦ୍ଧାରିତ ଲେନରେ ରୁହନ୍ତୁ।'
    },
    RAILWAY_TRANSIT: {
      en: 'Transit Tip: Use the foot overbridge or passenger walkway to reach your designated platform safely.',
      hi: 'यात्री सलाह: अपने प्लेटफॉर्म तक सुरक्षित पहुँचने के लिए पैदल उपरिगामी पुल (FOB) का उपयोग करें।',
      ta: 'பயணிகள் குறிப்பு: உங்கள் நடைமேடையை அடைய நடைமேம்பாலத்தை (FOB) பாதுகாப்பாகப் பயன்படுத்துங்கள்.',
      te: 'ప్రయాణీకుల సూచన: మీ ప్లాట్‌ఫాం చేరుకోవడానికి ఫుట్ ఓవర్‌బ్రిడ్జిని సురక్షితంగా ఉపయోగించండి.',
      bn: 'যাত্রী পরামর্শ: নির্দিষ্ট প্ল্যাটফর্মে পৌঁছাতে ফুট ওভারব্রিজ ব্যবহার করুন।',
      mr: 'प्रवासी सूचना: आपल्या प्लॅटफॉर्मवर जाण्यासाठी पादचारी पुलाचा (FOB) सुरक्षित वापर करा.',
      gu: 'મુસાફર સલાહ: તમારા પ્લેટફોર્મ પર પહોંચવા માટે ફૂટ ઓવરબ્રિજનો ઉપયોગ કરો.',
      kn: 'ಪ್ರಯಾಣಿಕರ ಸಲಹೆ: ನಿಮ್ಮ ಪ್ಲಾಟ್‌ಫಾರ್ಮ್ ತಲುಪಲು ಪಾದಚಾರಿ ಮೇಲ್ಸೇತುವೆಯನ್ನು ಬಳಸಿ.',
      ml: 'യാത്ര ടിപ്പ്: നിങ്ങളുടെ പ്ലാറ്റ്‌ഫോമിലെത്താൻ കാൽനട മേൽപ്പാലം ഉപയോഗിക്കുക.',
      pa: 'ਯਾਤਰੀ ਸਲਾਹ: ਆਪਣੇ ਪਲੇਟਫਾਰਮ ਤੇ ਪਹੁੰਚਣ ਲਈ ਪੈਦਲ ਓਵਰਬ੍ਰਿਜ ਦੀ ਵਰਤੋਂ ਕਰੋ।',
      or: 'ଯାତ୍ରୀ ପରାମର୍ଶ: ନିଜ ପ୍ଲାଟଫର୍ମକୁ ଯିବା ପାଇଁ ପାଦଚଲା ପୋଲ (FOB) ବ୍ୟବହାର କରନ୍ତୁ।'
    },
    COMMERCIAL: {
      en: 'Store Tip: Tap the telephone dialer card to directly call the establishment.',
      hi: 'दुकान सलाह: प्रतिष्ठान से सीधा संपर्क करने के लिए दिए गए फ़ोन नंबर बटन पर टैप करें।',
      ta: 'கடை குறிப்பு: நிறுவனத்தை நேரடியாக அழைக்க தொலைபேசி பொத்தானைத் தட்டவும்.',
      te: 'స్టోర్ సూచన: సంస్థను నేరుగా సంప్రదించడానికి ఫోన్ డయలర్ బటన్‌పై నొక్కండి.',
      bn: 'দোকান পরামর্শ: প্রতিষ্ঠানে সরাসরি যোগাযোগ করতে ফোন ডায়ালারে ট্যাপ করুন।',
      mr: 'दुकान सूचना: थेट संपर्क साधण्यासाठी फोन डायलर बटनावर टॅप करा.',
      gu: 'દુકાન સલાહ: સંસ્થાનો સીધો સંપર્ક કરવા માટે ફોન નંબર પર ટેપ કરો.',
      kn: 'ಅಂಗಡಿ ಸಲಹೆ: ಸಂಸ್ಥೆಯನ್ನು ನೇರವಾಗಿ ಸಂಪರ್ಕಿಸಲು ಫೋನ್ ಡಯಲರ್ ಬಳಸಿ.',
      ml: 'സ്റ്റോർ ടിപ്പ്: സ്ഥാപനവുമായി നേരിട്ട് ബന്ധപ്പെടാൻ ഫോൺ നമ്പറിൽ ടാപ്പ് ചെയ്യുക.',
      pa: 'ਸਟੋਰ ਸਲਾਹ: ਸੰਸਥਾ ਨਾਲ ਸਿੱਧਾ ਸੰਪਰਕ ਕਰਨ ਲਈ ਫ਼ੋਨ ਨੰਬਰ ਤੇ ਟੈਪ ਕਰੋ।',
      or: 'ଦୋକାନ ପରାମର୍ଶ: ସିଧାସଳଖ ଯୋଗାଯୋଗ କରିବାକୁ ଫୋନ୍ ଡାୟଲର ବଟନ ଟ୍ୟାପ କରନ୍ତୁ।'
    },
    CIVIC_PUBLIC: {
      en: 'Civic Tip: Public facility open for citizens and visitor access.',
      hi: 'नागरिक सलाह: आम जनता और आगंतुकों के लिए सार्वजनिक सुविधा उपलब्ध है।',
      ta: 'பொது உதவி குறிப்பு: பொதுமக்கள் மற்றும் பார்வையாளர்களுக்கான பொது வசதி.',
      te: 'పౌర సేవ సూచన: పౌరులు మరియు సందర్శకుల కోసం అందుబాటులో ఉన్న ప్రజా సదుపాయం.',
      bn: 'জনসেবা পরামর্শ: সর্বসাধারণের প্রবেশের জন্য উন্মুক্ত সরকারি/পৌর সেবা কেন্দ্র।',
      mr: 'नागरी सूचना: नागरिकांसाठी आणि पर्यटकांसाठी उपलब्ध सार्वजनिक सुविधा.',
      gu: 'નાગરિક સલાહ: મુલાકાતીઓ અને નાગરિકો માટે ઉપલબ્ધ જાહેર સુવિધા.',
      kn: 'ನಾಗರಿಕ ಸಲಹೆ: ಸಾರ್ವಜನಿಕರು ಮತ್ತು ಸಂದರ್ಶಕರಿಗಾಗಿ ತೆರೆದಿರುವ ಸೌಲಭ್ಯ.',
      ml: 'പൊതു ടിപ്പ്: സന്ദർശകർക്കായി തുറന്ന പൊതു സൗകര്യം.',
      pa: 'ਜਨਤਕ ਸਲਾਹ: ਆਮ ਲੋਕਾਂ ਲਈ ਉਪਲਬਧ ਜਨਤਕ ਸੁਵਿਧਾ।',
      or: 'ସର୍ବସାଧାରଣ ପରାମର୍ଶ: ନାଗରିକ ଏବଂ ଦର୍ଶକଙ୍କ ପାଇଁ ଉଦ୍ଦିଷ୍ଟ ସରକାରୀ ସୁବିଧା।'
    }
  };

  if (domain === 'ROAD_HIGHWAY') {
    domainLabel = '🛣️ Highway & Road Wayfinding';
    domainIcon = 'route';

    // Extract route number
    const routeMatch = combined.match(/\b(NH[\s-]?\d+|SH[\s-]?\d+|Expressway)\b/i);
    if (routeMatch) keyDetails.push(`Route: ${routeMatch[0].toUpperCase()}`);

    // Extract distances
    const distanceMatch = combined.match(/\b\d+\s*(?:km|kms|m|মিটার|किमी|कि\.मी\.)\b/gi);
    if (distanceMatch) keyDetails.push(...distanceMatch.slice(0, 2).map((d) => `Distance: ${d}`));

    // Speed limits
    const speedMatch = combined.match(/speed\s*limit\s*\d+/i);
    if (speedMatch) keyDetails.push(speedMatch[0].toUpperCase());

    if (direction) keyDetails.push(`Lane: ${direction}`);
  } else if (domain === 'RAILWAY_TRANSIT') {
    domainLabel = '🚆 Railway & Station Transit';
    domainIcon = 'train';

    const pfMatch = combined.match(/\b(Platform[\s-]?\d+|PF[\s-]?\d+|प्लेटफॉर्म[\s-]?\d+)\b/i);
    if (pfMatch) keyDetails.push(pfMatch[0]);

    if (/fob|foot\s*overbridge|overbridge/i.test(combined)) {
      keyDetails.push('Foot Overbridge Access');
    }
    if (/waiting\s*hall|waiting\s*room/i.test(combined)) {
      keyDetails.push('Waiting Hall');
    }
    if (/ticket\s*counter|ticket/i.test(combined)) {
      keyDetails.push('Ticket Booking Counter');
    }
    if (direction) keyDetails.push(`Direction: ${direction}`);
  } else if (domain === 'CIVIC_PUBLIC') {
    domainLabel = '🏛️ Civic & Public Facility';
    domainIcon = 'landmark';
    if (direction) keyDetails.push(`Location: ${direction}`);
    if (hours) keyDetails.push(`Hours: ${hours}`);
  } else {
    domainLabel = '🛍️ Commercial Retail & Services';
    domainIcon = 'store';
    if (direction) keyDetails.push(`Direction: ${direction}`);
    if (hours) keyDetails.push(`Timings: ${hours}`);
  }

  guidanceTip =
    guidanceMessages[domain]?.[targetLang] ||
    guidanceMessages[domain]?.en ||
    'Follow directional signs safely.';

  return {
    domain,
    domainLabel,
    domainIcon,
    keyDetails,
    guidanceTip
  };
}

/**
 * Conjunction words for list items across all 22 official languages + English.
 */
const LANGUAGE_CONJUNCTIONS: Record<string, string> = {
  en: 'and',
  hi: 'और',
  ta: 'மற்றும்',
  te: 'మరియు',
  bn: 'এবং',
  mr: 'आणि',
  gu: 'અને',
  kn: 'ಮತ್ತು',
  ml: 'കൂടാതെ',
  pa: 'ਅਤੇ',
  or: 'ଏବଂ',
  as: 'আৰু',
  ur: 'اور',
  mai: 'आ',
  kok: 'आनी',
  sat: 'ᱟᱨ',
  mni: 'ꯑꯃꯁᱩꯡ',
  ks: 'تہ',
  ne: 'र',
  doi: 'ते',
  brx: 'आरो',
  sa: 'च',
  sd: '۽'
};

/**
 * Formats a list of tokens into a natural sentence list with language-specific conjunction.
 */
function joinWithConjunction(tokens: string[], lang: string): string {
  if (tokens.length === 0) return '';
  if (tokens.length === 1) return tokens[0];
  const conj = LANGUAGE_CONJUNCTIONS[lang] || 'and';
  if (tokens.length === 2) return `${tokens[0]} ${conj} ${tokens[1]}`;
  const allExceptLast = tokens.slice(0, -1).join(', ');
  const last = tokens[tokens.length - 1];
  return `${allExceptLast} ${conj} ${last}`;
}

/**
 * Built-in intelligent Gemma conversational synthesizer supporting all 11 languages
 * across Road, Railway, Commercial, and Civic signboards.
 */
export function synthesizeMultilingualWithLocalGemma(
  input: GemmaSummarizeInput,
  domain: SignboardDomain
): string {
  const lang = input.targetLanguage || 'en';
  const { cleanBrand, cleanServices } = deduplicateSignboardContent(
    input.brandName,
    input.servicesText
  );
  const direction = (input.directionGuide || '').trim();
  const rawHours = (input.hoursText || '').trim();

  // Safety guard: If both brand and services are empty, do not generate fake text
  if (!cleanBrand && !cleanServices) {
    const emptyNotices: Record<string, string> = {
      en: 'No readable text was detected on this signboard.',
      hi: 'साइनबोर्ड पर कोई स्पष्ट पाठ नहीं मिला।',
      ta: 'பலகையில் எந்த உரையும் கண்டறியப்படவில்லை.',
      te: 'సైన్‌బోర్డ్‌పై ఎలాంటి వచనం కనుగొనబడలేదు.',
      bn: 'সাইনবোর্ডে কোনো পাঠ্য সনাক্ত করা যায়নি।',
      mr: 'फलकावर कोणताही मजकूर आढळला नाही.',
      gu: 'સાઇનબોર્ડ પર કોઈ લખાણ મળ્યું નથી.',
      kn: 'ಸೈನ್‌ಬೋರ್ಡ್‌ನಲ್ಲಿ ಯಾವುದೇ ಪಠ್ಯ ಕಂಡುಬಂದಿಲ್ಲ.',
      ml: 'ബോർഡിൽ വാചകങ്ങളൊന്നും കണ്ടെത്തിയില്ല.',
      pa: 'ਸਾਈਨਬੋਰਡ ਤੇ ਕੋਈ ਲਿਖਤ ਨਹੀਂ ਮਿਲੀ।',
      or: 'ସାଇନବୋର୍ଡରେ କୌଣସି ଲେଖା ମିଳିଲା ନାହିଁ।'
    };
    return emptyNotices[lang] || emptyNotices.en;
  }

  const serviceTokens = cleanServices
    .split(/[•.,|;\n]+/)
    .map((s) => s.trim())
    .filter(
      (s) =>
        s.length > 1 &&
        !/^(time|timing|reg|shop|ph|phone|1234567890|9876543210)/i.test(s) &&
        !/(?:corel|cdr|photoshop|psd|amangraphics|www\.)/i.test(s)
    );

  const fluentServices = joinWithConjunction(serviceTokens, lang);

  // 1. ROAD / HIGHWAY WAYFINDING
  if (domain === 'ROAD_HIGHWAY') {
    const route = cleanBrand || 'मार्ग';
    const destination = fluentServices || cleanServices;
    const dir = direction ? `${direction}, ` : '';

    const isCivicStreetPlaque =
      /(?:^|[\s.,:;\-_/|])(?:ward|zone|division|area|वार्ड|क्षेत्र|मंडल|வட்டம்|மண்டலம்|வார்டு|பகுதி|வீதி|వార్డు|गली|தெரு|street|lane|cross|nagar|நகர்|नगर)(?:$|[\s.,:;\-_/|])/iu.test(
        `${route} ${destination}`
      ) && !/\b\d+\s*(?:km|kms|m|किमी|कि\.मी\.|মিটার|கி\.மீ)\b/i.test(destination);

    if (isCivicStreetPlaque) {
      const detailsSuffix = destination ? `, ${destination}` : '';
      switch (lang) {
        case 'hi':
          return `${dir}यह ${route} है${detailsSuffix}।`.trim();
        case 'ta':
          return `${dir}இது ${route}${detailsSuffix}.`.trim();
        case 'te':
          return `${dir}ఇది ${route}${detailsSuffix}.`.trim();
        case 'bn':
          return `${dir}এটি ${route}${detailsSuffix}।`.trim();
        case 'mr':
          return `${dir}हा ${route} आहे${detailsSuffix}.`.trim();
        case 'gu':
          return `${dir}આ ${route} છે${detailsSuffix}.`.trim();
        case 'kn':
          return `${dir}ಇದು ${route}${detailsSuffix}.`.trim();
        case 'ml':
          return `${dir}ഇത് ${route} ആണ്${detailsSuffix}.`.trim();
        case 'pa':
          return `${dir}ਇਹ ${route} ਹੈ${detailsSuffix}।`.trim();
        case 'or':
          return `${dir}ଏହା ${route}${detailsSuffix}।`.trim();
        default:
          return `${dir}This is ${route}${detailsSuffix}.`.trim();
      }
    }

    switch (lang) {
      case 'hi':
        return `${dir}${route} पर आगे ${destination} की ओर जाएं।`.trim();
      case 'ta':
        return `${dir}${route} வழியாக ${destination} நோக்கிச் செல்லுங்கள்.`.trim();
      case 'te':
        return `${dir}${route} ద్వారా ${destination} వైపు వెళ్ళండి.`.trim();
      case 'bn':
        return `${dir}${route} ধরে ${destination} এর দিকে এগিয়ে যান।`.trim();
      case 'mr':
        return `${dir}${route} वरून ${destination} कडे पुढे जा.`.trim();
      case 'gu':
        return `${dir}${route} પર આગળ ${destination} તરફ જાઓ.`.trim();
      case 'kn':
        return `${dir}${route} ಮೂಲಕ ${destination} ಕಡೆಗೆ ಮುಂದುವರಿಯಿರಿ.`.trim();
      case 'ml':
        return `${dir}${route} വഴി ${destination} ഭാഗത്തേക്ക് പോകുക.`.trim();
      case 'pa':
        return `${dir}${route} ਰਾਹੀਂ ${destination} ਵੱਲ ਅੱਗੇ ਵਧੋ।`.trim();
      case 'or':
        return `${dir}${route} ଦେଇ ${destination} ଆଡ଼କୁ ଆଗକୁ ବଢ଼ନ୍ତୁ।`.trim();
      default:
        return `${dir}Follow ${route} towards ${destination}.`.trim();
    }
  }

  // 2. RAILWAY / TRANSIT STATION
  if (domain === 'RAILWAY_TRANSIT') {
    const platform = cleanBrand || 'प्लेटफॉर्म';
    const amenities = fluentServices || cleanServices;
    const dir = direction ? `${direction} ` : '';

    switch (lang) {
      case 'hi':
        return amenities
          ? `${dir}${platform} स्थित है, जहाँ ${amenities} की सुविधा है।`.trim()
          : `${dir}${platform} की ओर जाएं।`.trim();
      case 'ta':
        return amenities
          ? `${dir}${platform} உள்ளது, அங்கு ${amenities} வசதி உள்ளது.`.trim()
          : `${dir}${platform} நோக்கிச் செல்லவும்.`.trim();
      case 'te':
        return amenities
          ? `${dir}${platform} ఉంది, అక్కడ ${amenities} సౌకర్యం ఉంది.`.trim()
          : `${dir}${platform} వైపు వెళ్ళండి.`.trim();
      case 'bn':
        return amenities
          ? `${dir}${platform} অবস্থিত, যেখানে ${amenities} রয়েছে।`.trim()
          : `${dir}${platform} এর দিকে যান।`.trim();
      case 'mr':
        return amenities
          ? `${dir}${platform} आहे, जिथे ${amenities} ची सोय आहे.`.trim()
          : `${dir}${platform} कडे जा.`.trim();
      case 'gu':
        return amenities
          ? `${dir}${platform} છે, જ્યાં ${amenities} ની સુવિધા છે.`.trim()
          : `${dir}${platform} તરફ જાઓ.`.trim();
      case 'kn':
        return amenities
          ? `${dir}${platform} ಇದೆ, ಅಲ್ಲಿ ${amenities} ಸೌಲಭ್ಯವಿದೆ.`.trim()
          : `${dir}${platform} ಕಡೆಗೆ ಸಾಗಿ.`.trim();
      case 'ml':
        return amenities
          ? `${dir}${platform} ഉണ്ട്, അവിടെ ${amenities} സൗകര്യം ലഭ്യമാണ്.`.trim()
          : `${dir}${platform} ഭാഗത്തേക്ക് പോകുക.`.trim();
      case 'pa':
        return amenities
          ? `${dir}${platform} ਹੈ, ਜਿੱਥੇ ${amenities} ਦੀ ਸਹੂਲਤ ਹੈ।`.trim()
          : `${dir}${platform} ਵੱਲ ਜਾਓ।`.trim();
      case 'or':
        return amenities
          ? `${dir}${platform} ଅବସ୍ଥିତ, ଯେଉଁଠାରେ ${amenities} ସୁବିଧା ଅଛି।`.trim()
          : `${dir}${platform} ଆଡ଼କୁ ଯାଆନ୍ତୁ।`.trim();
      default:
        return amenities
          ? `${dir}${platform} is located, offering ${amenities}.`.trim()
          : `${dir}Proceed towards ${platform}.`.trim();
    }
  }

  // 3. COMMERCIAL / CLINIC / SHOP / CIVIC (Default)
  const dirPrefix = direction ? `${direction} ` : '';
  let hoursText = '';
  if (rawHours) {
    if (/24[xX/]7/i.test(rawHours)) {
      hoursText = lang === 'hi' ? '24 घंटे ' : '24x7 ';
    }
  }

  switch (lang) {
    case 'hi':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} स्थित है, जहाँ ${fluentServices} की सुविधा ${hoursText}उपलब्ध है।`.trim()
          : `${cleanBrand} में ${fluentServices} की सुविधा ${hoursText}उपलब्ध है।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} स्थित है।`.trim();

    case 'ta':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} உள்ளது, இங்கு ${fluentServices} சேவைகள் கிடைக்கின்றன.`.trim()
          : `${cleanBrand}ல் ${fluentServices} சேவைகள் கிடைக்கின்றன.`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} உள்ளது.`.trim();

    case 'te':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} ఉంది, ఇక్కడ ${fluentServices} అందుబాటులో ఉన్నాయి.`.trim()
          : `${cleanBrand}లో ${fluentServices} సేవలు అందుబాటులో ఉన్నాయి.`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} ఉంది.`.trim();

    case 'bn':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} রয়েছে, যেখানে ${fluentServices} পাওয়া যায়।`.trim()
          : `${cleanBrand}-এ ${fluentServices} এর সুবিধা রয়েছে।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} অবস্থিত।`.trim();

    case 'mr':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} आहे, जिथे ${fluentServices} ची सुविधा उपलब्ध आहे.`.trim()
          : `${cleanBrand} येथे ${fluentServices} ची सुविधा उपलब्ध आहे.`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} आहे.`.trim();

    case 'gu':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} આવેલું છે, જ્યાં ${fluentServices} ની સુવિધા ઉપલબ્ધ છે.`.trim()
          : `${cleanBrand} માં ${fluentServices} ની સુવિધા ઉપલબ્ધ છે.`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} આવેલું છે.`.trim();

    case 'kn':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} ಇದೆ, ಇಲ್ಲಿ ${fluentServices} ಲಭ್ಯವಿದೆ.`.trim()
          : `${cleanBrand} ನಲ್ಲಿ ${fluentServices} ಸೌಲಭ್ಯವಿದೆ.`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} ಇದೆ.`.trim();

    case 'ml':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} സ്ഥിതിചെയ്യുന്നു, ഇവിടെ ${fluentServices} ലഭ്യമാണ്.`.trim()
          : `${cleanBrand}ൽ ${fluentServices} ലഭ്യമാണ്.`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} സ്ഥിതിചെയ്യുന്നു.`.trim();

    case 'pa':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} ਸਥਿਤ ਹੈ, ਜਿੱਥੇ ${fluentServices} ਦੀ ਸਹੂਲਤ ਉਪਲਬਧ ਹੈ।`.trim()
          : `${cleanBrand} ਵਿੱਚ ${fluentServices} ਦੀ ਸਹੂਲਤ ਉਪਲਬਧ ਹੈ।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} ਸਥਿਤ ਹੈ।`.trim();

    case 'or':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} ଅବସ୍ଥିତ, ଯେଉଁଠାରେ ${fluentServices} ସୁବିଧା ଉପଲବ୍ଧ।`.trim()
          : `${cleanBrand} ରେ ${fluentServices} ସୁବିଧା ଉପଲବ୍ଧ।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} ଅବସ୍ଥିତ।`.trim();

    case 'as':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} অৱস্থিত, য'ত ${fluentServices} উপলব্ধ।`.trim()
          : `${cleanBrand}ত ${fluentServices}ৰ সুবিধা উপলব্ধ।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} অৱস্থিত।`.trim();

    case 'ur':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} واقع ہے، جہاں ${fluentServices} کی سہولت دستیاب ہے۔`.trim()
          : `${cleanBrand} میں ${fluentServices} کی سہولت دستیاب ہے۔`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} واقع ہے۔`.trim();

    case 'mai':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} स्थित अछि, जतय ${fluentServices} क सुविधा उपलब्ध अछि।`.trim()
          : `${cleanBrand} मे ${fluentServices} क सुविधा उपलब्ध अछि।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} अछि।`.trim();

    case 'kok':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} आसा, थंय ${fluentServices} ची सोय आसा।`.trim()
          : `${cleanBrand} हांगा ${fluentServices} ची सोय उपलब्ध आसा।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} आसा।`.trim();

    case 'ne':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} अवस्थित छ, जहाँ ${fluentServices} को सुविधा उपलब्ध छ।`.trim()
          : `${cleanBrand} मा ${fluentServices} को सुविधा उपलब्ध छ।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} अवस्थित छ।`.trim();

    case 'sa':
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} अस्ति, यत्र ${fluentServices} सौविध्यम् अस्ति।`.trim()
          : `${cleanBrand} मध्ये ${fluentServices} सुविधा वर्तते।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} अस्ति।`.trim();

    case 'brx': // Bodo (Devanagari)
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} दं, बेवहाय ${fluentServices} नि सुबिदा दं।`.trim()
          : `${cleanBrand}-आव ${fluentServices} नि सुबिदा दं।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} दं।`.trim();

    case 'sat': // Santali (Ol Chiki)
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} ᱢᱮᱱᱟᱜᱼᱟ, ᱱᱚᱰᱮ ${fluentServices} ᱨᱮᱱᱟᱜ ᱥᱩᱵᱤᱫᱷᱟ ᱢᱮᱱᱟᱜᱼᱟ᱾`.trim()
          : `${cleanBrand} ᱨᱮ ${fluentServices} ᱨᱮᱱᱟᱜ ᱥᱩᱵᱤᱫᱷᱟ ᱢᱮᱱᱟᱜᱼᱟ᱾`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} ᱢᱮᱱᱟᱜᱼᱟ᱾`.trim();

    case 'doi': // Dogri
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} ऐ, जित्थै ${fluentServices} दी सुविधा ऐ।`.trim()
          : `${cleanBrand} च ${fluentServices} दी सुविधा ऐ।`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} ऐ।`.trim();

    case 'mni': // Manipuri (Meitei Mayek)
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} ꯂꯩꯔꯤ, ꯃꯐꯝ ꯑꯁꯤꯗꯥ ${fluentServices} ꯒꯤ ꯈꯨꯗꯣꯡꯆꯥꯕꯥ ꯂꯩꯔꯤ꯫`.trim()
          : `${cleanBrand}-ꯗꯥ ${fluentServices} ꯒꯤ ꯈꯨꯗꯣꯡꯆꯥꯕꯥ ꯂꯩꯔꯤ꯫`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} ꯂꯩꯔꯤ꯫`.trim();

    case 'ks': // Kashmiri (Perso-Arabic)
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} چھُ، یَتھ منٛز ${fluentServices} ہٕنٛز سَہوٗلَتھ دַستیاب چھِ۔`.trim()
          : `${cleanBrand} منٛز چھِ ${fluentServices} ہٕنٛز سَہوٗلَتھ دַستیاب۔`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} چھُ۔`.trim();

    case 'sd': // Sindhi (Arabic)
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} آهي، جتي ${fluentServices} جي سهولت موجود آهي.`.trim()
          : `${cleanBrand} ۾ ${fluentServices} جي سهولت موجود آهي.`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices} آهي.`.trim();

    default: // 'en', or other regional scripts
      if (cleanBrand && fluentServices) {
        return direction
          ? `${dirPrefix}${cleanBrand} is located, offering ${fluentServices}.`.trim()
          : `${cleanBrand} offers ${fluentServices}.`.trim();
      }
      return `${dirPrefix}${cleanBrand || fluentServices}.`.trim();
  }
}

interface GemmaCopilotResponse {
  spokenSentence: string;
  signCategory?: string;
  archetype?: 'COMMERCIAL_STORE' | 'STREET_WAYFINDING' | 'TRANSIT_REGULATORY';
  name?: string;
  services?: string[];
  timings?: string;
  urgentNotice?: string;
  address?: string;
  phoneNumbers?: string[];
  keyFacts?: string[];
  actionableTip?: string;
}

/**
 * Resilient JSON and text parser for Gemma copilot output.
 * Handles pure JSON, markdown fenced JSON, or direct plain-text sentence fallbacks.
 */
function parseGemmaCopilotOutput(rawText: string, fallbackScript: string): GemmaCopilotResponse {
  let text = (rawText || '').trim();
  // Strip outer quotes if model wrapped its entire response in quotes (e.g. ""{...}"")
  text = text.replace(/^["'«“\s]+/, '').replace(/["'»”\s]+$/, '').trim();

  // Strip markdown code fences
  if (text.startsWith('```')) {
    text = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
  }

  // Look for JSON object in text
  const jsonMatch = text.match(/\{[\s\S]*\}/);
  if (jsonMatch) {
    try {
      const parsed = JSON.parse(jsonMatch[0]);
      const sentence =
        parsed.spokenSentence ||
        parsed.spoken_sentence ||
        parsed.sentence ||
        parsed.speech ||
        fallbackScript;

      const services = Array.isArray(parsed.services)
        ? parsed.services.map(String).filter((s: string) => s.length > 0)
        : Array.isArray(parsed.offerings)
        ? parsed.offerings.map(String).filter((s: string) => s.length > 0)
        : [];

      const phoneNumbers = Array.isArray(parsed.phoneNumbers)
        ? parsed.phoneNumbers.map(String).filter((s: string) => s.length > 0)
        : Array.isArray(parsed.phones)
        ? parsed.phones.map(String).filter((s: string) => s.length > 0)
        : [];

      const facts = Array.isArray(parsed.keyFacts)
        ? parsed.keyFacts
        : Array.isArray(parsed.key_points)
        ? parsed.key_points
        : Array.isArray(parsed.keyDetails)
        ? parsed.keyDetails
        : [];

      let arch: 'COMMERCIAL_STORE' | 'STREET_WAYFINDING' | 'TRANSIT_REGULATORY' | undefined = undefined;
      const rawArch = String(parsed.archetype || parsed.archeType || '').toUpperCase();
      if (rawArch.includes('STREET') || rawArch.includes('WAYFIND') || rawArch.includes('ROAD') || rawArch.includes('HIGHWAY')) {
        arch = 'STREET_WAYFINDING';
      } else if (rawArch.includes('TRANSIT') || rawArch.includes('RAIL') || rawArch.includes('REGULATORY')) {
        arch = 'TRANSIT_REGULATORY';
      } else if (rawArch.includes('COMMERCIAL') || rawArch.includes('STORE') || rawArch.includes('SHOP') || rawArch.includes('HOSPITAL') || rawArch.includes('CLINIC')) {
        arch = 'COMMERCIAL_STORE';
      }

      return {
        spokenSentence: String(sentence).replace(/^["'«“]/, '').replace(/["'»”]$/, '').trim(),
        signCategory: parsed.signCategory || parsed.sign_category || parsed.category || undefined,
        archetype: arch,
        name: parsed.name || parsed.brandName || parsed.title || undefined,
        services: services.length > 0 ? services : undefined,
        timings: parsed.timings || parsed.hours || parsed.schedule || undefined,
        urgentNotice: parsed.urgentNotice || parsed.mandatoryNotice || parsed.notice || parsed.alert || undefined,
        address: parsed.address || parsed.location || undefined,
        phoneNumbers: phoneNumbers.length > 0 ? phoneNumbers : undefined,
        keyFacts: facts.map(String).filter((s: string) => s.length > 0),
        actionableTip: parsed.actionableTip || parsed.actionable_tip || parsed.tip || undefined
      };
    } catch {
      // JSON parse failed, proceed to plain text handling
    }
  }

  // Fallback: If model returned a direct plain sentence
  const cleanPlain = text.replace(/^["'«“]/, '').replace(/["'»”]$/, '').trim();
  return {
    spokenSentence: cleanPlain || fallbackScript,
    signCategory: undefined,
    keyFacts: [],
    actionableTip: undefined
  };
}

/**
 * Master Gemma Conversational Summarizer for all 11 Indian languages.
 * Implements domain-specific guidelines (Roads, Railways, Commercial, Civic),
 * deduplicates repeated brand/services, and generates extra context for the user.
 */
export async function summarizeWithGemma(
  input: GemmaSummarizeInput,
  settings: AppSettings
): Promise<GemmaSummarizeResult> {
  const startTime = performance.now();
  const targetLang = input.targetLanguage || settings.targetLanguage || 'en';
  const langObj = getLanguageByCode(targetLang);

  // 1. Detect domain (Road, Railway, Commercial, Civic)
  const domain = detectSignboardDomain(
    input.brandName,
    input.servicesText,
    input.rawBlocks,
    input.archetype
  );

  // 2. Deduplicate content (stop duplicate repetition of brand/lines)
  const { cleanBrand, cleanServices } = deduplicateSignboardContent(
    input.brandName,
    input.servicesText
  );

  // Safety guard: If OCR produced no readable text, do NOT call LLMs (prevents hallucinations)
  if (!cleanBrand && !cleanServices) {
    const emptyGuidance: Record<string, string> = {
      en: 'No readable text was detected on the signboard. Please retake the photo with the signboard centered and well-lit.',
      hi: 'साइनबोर्ड पर कोई स्पष्ट पाठ नहीं मिला। कृपया साइनबोर्ड को फ़्रेम के बीच में रखकर अच्छी रोशनी में दोबारा फ़ोटो लें।',
      ta: 'பலகையில் எந்த உரையும் கண்டறியப்படவில்லை. பலகையை மையப்படுத்தி மீண்டும் படம் எடுக்கவும்.',
      te: 'సైన్‌బోర్డ్‌పై ఎలాంటి వచనం కనుగొనబడలేదు. దయచేసి సైన్‌బోర్డ్‌ను స్పష్టంగా మళ్ళీ ఫోటో తీయండి.',
      bn: 'সাইনবোর্ডে কোনো পাঠ্য সনাক্ত করা যায়নি। অনুগ্রহ করে সাইনবোর্ডটি ফ্রেমের মাঝে রেখে পরিষ্কার ছবি তুলুন।',
      mr: 'फलकावर कोणताही मजकूर आढळला नाही. कृपया फलक मध्यभागी ठेवून पुन्हा स्पष्ट फोटो काढा.',
      gu: 'સાઇનબોર્ડ પર કોઈ વાંચી શકાય તેવું લખાણ મળ્યું નથી. સાઇનબોર્ડનો સ્પષ્ટ ફોટો ફરીથી લો.',
      kn: 'ಸೈನ್‌ಬೋರ್ಡ್‌ನಲ್ಲಿ ಯಾವುದೇ ಪಠ್ಯ ಕಂಡುಬಂದಿಲ್ಲ. ದಯವಿಟ್ಟು ಸ್ಪಷ್ಟವಾಗಿ ಮತ್ತೆ ಫೋಟೋ ತೆಗೆದುಕೊಳ್ಳಿ.',
      ml: 'ബോർഡിൽ വാചകങ്ങളൊന്നും കണ്ടെത്തിയില്ല. ബോർഡ് വ്യക്തമായി വീണ്ടും പകർത്തുക.',
      pa: 'ਸਾਈਨਬੋਰਡ ਤੇ ਕੋਈ ਲਿਖਤ ਨਹੀਂ ਮਿਲੀ। ਕਿਰਪਾ ਕਰਕੇ ਸਾਫ਼ ਰੋਸ਼ਨੀ ਵਿੱਚ ਦੁਬਾਰਾ ਫ਼ੋਟੋ ਲਓ।',
      or: 'ସାଇନବୋର୍ଡରେ କୌଣସି ଲେଖା ମିଳିଲା ନାହିଁ। ଦୟାକରି ସ୍ପଷ୍ଟ ଭାବରେ ପୁଣି ଫଟୋ ଉଠାନ୍ତୁ।'
    };
    const alertMsg = emptyGuidance[targetLang] || emptyGuidance.en;

    return {
      synthesizedScript: alertMsg,
      rawConcatenation: '',
      source: 'builtin',
      modelUsed: 'Safety Guard (Empty OCR)',
      domain: 'COMMERCIAL',
      extraContext: {
        domain: 'COMMERCIAL',
        domainLabel: '⚠️ No Signboard Text Detected',
        domainIcon: 'alert',
        keyDetails: ['0 text blocks detected', 'Signboard unreadable or occluded'],
        guidanceTip: alertMsg
      },
      processingTimeMs: Math.round(performance.now() - startTime)
    };
  }

  // 3. Extract extra user context
  const extraContext = extractExtraContext(
    domain,
    cleanBrand,
    cleanServices,
    input.directionGuide || '',
    input.hoursText || '',
    targetLang
  );

  // Baseline mechanical concatenation for comparison
  const rawDirection = input.directionGuide ? `${input.directionGuide}, ` : '';
  const mainPart = cleanBrand || input.brandName;
  const secondaryPart = cleanServices;
  const rawConcatenation = secondaryPart
    ? `${rawDirection}${mainPart}. ${secondaryPart}`.trim()
    : `${rawDirection}${mainPart}`.trim();

  const provider = settings.gemmaProvider || 'builtin';
  const apiKey = (settings.gemmaApiKey || '').trim();

  // If live external provider requested and key is present, invoke external LLM
  if (provider !== 'builtin' && apiKey) {
    try {
      let endpoint = '';
      let defaultModel = '';

      if (provider === 'groq') {
        endpoint = 'https://api.groq.com/openai/v1/chat/completions';
        defaultModel =
          !settings.gemmaModel ||
          settings.gemmaModel === 'gemma2-9b-it'
            ? 'openai/gpt-oss-20b'
            : settings.gemmaModel.trim();
      } else if (provider === 'openrouter') {
        endpoint = 'https://openrouter.ai/api/v1/chat/completions';
        defaultModel = settings.gemmaModel?.trim() || 'google/gemma-2-9b-it';
      } else {
        endpoint = settings.gemmaEndpoint || 'https://api.openai.com/v1/chat/completions';
        defaultModel = settings.gemmaModel?.trim() || 'llama-3.3-70b-versatile';
      }

      // Prepare sanitized signboard text lines (purging GSTIN, licenses & mixed serial codes)
      const rawLines = (input.rawBlocks || [])
        .map((t) => sanitizeGibberishAndCodes(t.trim()))
        .filter((t) => t.length > 1);

      const signboardText =
        rawLines.length > 0
          ? rawLines.map((line, idx) => `${idx + 1}. "${line}"`).join('\n')
          : [cleanBrand ? `1. "${cleanBrand}"` : '', cleanServices ? `2. "${cleanServices}"` : '']
              .filter(Boolean)
              .join('\n');

      const spatialHint = input.directionGuide ? `\nSpatial / Lane Direction Detected: ${input.directionGuide}` : '';

      // Specific anti-Hindi guardrail for regional/classical languages that share Devanagari or are distinct
      let languageSpecificGuard = '';
      if (targetLang === 'mai') {
        languageSpecificGuard = `\nCRITICAL LANGUAGE MANDATE: You MUST output in genuine MAITHILI (मैथिली). DO NOT OUTPUT HINDI! Use Maithili verb forms ('अछि', 'छै', 'जतय', 'मे') instead of Hindi ('है', 'हैं', 'जहाँ', 'में').`;
      } else if (targetLang === 'sa') {
        languageSpecificGuard = `\nCRITICAL LANGUAGE MANDATE: You MUST output in genuine SANSKRIT (संस्कृतम्). DO NOT OUTPUT HINDI! Use Sanskrit inflections and verbs ('अस्ति', 'सन्ति', 'यत्र', 'मध्ये') instead of Hindi ('है', 'हैं', 'जहाँ', 'में').`;
      } else if (targetLang === 'kok') {
        languageSpecificGuard = `\nCRITICAL LANGUAGE MANDATE: You MUST output in genuine KONKANI (कोंकणी). DO NOT OUTPUT HINDI! Use Konkani verb forms ('आसा', 'थंय', 'हांगा') instead of Hindi ('है', 'जहाँ').`;
      } else if (targetLang === 'doi') {
        languageSpecificGuard = `\nCRITICAL LANGUAGE MANDATE: You MUST output in genuine DOGRI (डोगरी). DO NOT OUTPUT HINDI! Use Dogri markers ('ऐ', 'च') instead of Hindi ('है', 'में').`;
      } else if (targetLang === 'ne') {
        languageSpecificGuard = `\nCRITICAL LANGUAGE MANDATE: You MUST output in genuine NEPALI (नेपाली). DO NOT OUTPUT HINDI! Use Nepali verb forms ('छ', 'छन्', 'मा') instead of Hindi ('है', 'में').`;
      } else if (targetLang !== 'hi' && targetLang !== 'en') {
        languageSpecificGuard = `\nCRITICAL LANGUAGE MANDATE: You MUST output in genuine ${langObj.name} (${langObj.nativeName}). DO NOT default or fallback to Hindi!`;
      }

      const systemPrompt = `You are an intelligent, assistive travel copilot helping someone navigate Indian streets, markets, and highways in real-time.
The user just pointed their camera at an outdoor signboard.
You are given the exact text lines extracted from the signboard.

Target Language: ${langObj.name} (${langObj.nativeName})${languageSpecificGuard}

CRITICAL MANDATORY INSTRUCTIONS:
1. OUTPUT ONLY ONE clean, direct conversational spoken sentence in ${langObj.name} script for audio playback in user's earbuds.
2. ABSOLUTE FILTER - REMOVE ALL STATUTORY & LICENSE CODES:
   Never speak, mention, or include any:
   - Registration numbers, Trade licenses, Shop Act / Gumasta numbers (e.g. "Reg No", "Lic No: 123/45", "DL No", "TL No")
   - Tax IDs, GST / GSTIN numbers, TIN, CIN, PAN, TAN numbers
   - FSSAI food license numbers, Drug license numbers (e.g. "20B", "21B")
   - Mixed alphanumeric serial codes (e.g. "27AAAAA0000A1Z5", "DL20B149298")
   - Flex banner printing watermarks, Corel CDR/Photoshop artifacts, designer URLs (e.g. "amangraphics.com", "Editable with Font"), or fake placeholder phone numbers (e.g. "1234567890")
3. GIBBERISH & NOISE RE-VERIFICATION:
   Re-verify every word before producing the final text:
   - If a word or sequence consists of broken OCR characters, random punctuation, or meaningless garbage letters, completely purge it.
   - Do NOT output raw JSON keys, DO NOT output markdown blocks, DO NOT explain yourself.
   - Output DIRECTLY AND ONLY: the natural spoken sentence in ${langObj.name}.

COPILOT CONTENT PRINCIPLES:
- Conversational & Human: Speak like a knowledgeable local friend walking beside the user.
- Core Priorities for Commercial & Healthcare Establishments (Stores, Hospitals, Clinics, Dispensaries, Restaurants):
  1. PRIMARY FOCUS: State the establishment name and its primary offerings/services.
  2. SCHEDULE & TIMINGS: Announce operating hours (e.g. "सुबह 6:30 से रात 10 बजे तक खुला है") or 24/7 availability if mentioned.
  3. MANDATORY NOTICES: State critical emergency notices (e.g. "Emergency Casualty Entrance", "Appointments Only") if present.
  4. ADDRESS POLICY: NEVER recite postal door numbers (e.g. "No: 1121", "2/60") in spoken audio. Keep speech focused on offerings and timings!
- Zero Bilingual Stutter: Pronounce repeated bilingual names ONCE only.`;

      const userPrompt = `Signboard Text Detected:
${signboardText}${spatialHint}

Re-verify and filter out all statutory licenses, tax codes, and OCR gibberish. Output ONLY the clean, final spoken sentence in ${langObj.name} (strictly native vocabulary, NOT Hindi):`;

      const res = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          model: defaultModel,
          messages: [
            { role: 'system', content: systemPrompt },
            { role: 'user', content: userPrompt }
          ],
          temperature: 0.25,
          max_tokens: 350
        })
      });

      if (res.ok) {
        const data = await res.json();
        const rawContent = data.choices?.[0]?.message?.content?.trim();
        if (rawContent) {
          const copilotData = parseGemmaCopilotOutput(rawContent, rawConcatenation);

          // Update extraContext with LLM-discovered intelligence
          if (copilotData.signCategory) {
            extraContext.domainLabel = copilotData.signCategory;
          }
          if (copilotData.keyFacts && copilotData.keyFacts.length > 0) {
            extraContext.keyDetails = copilotData.keyFacts;
          }
          if (copilotData.actionableTip) {
            extraContext.guidanceTip = copilotData.actionableTip;
          }

          // Build GemmaIdentificationResult
          const detectedArch = copilotData.archetype || (domain === 'ROAD_HIGHWAY' ? 'STREET_WAYFINDING' : domain === 'RAILWAY_TRANSIT' ? 'TRANSIT_REGULATORY' : 'COMMERCIAL_STORE');
          const identification: GemmaIdentificationResult = {
            signCategory: copilotData.signCategory || extraContext.domainLabel,
            archetype: detectedArch,
            name: copilotData.name || cleanBrand || input.brandName,
            services: copilotData.services && copilotData.services.length > 0 ? copilotData.services : [cleanServices || input.servicesText].filter(Boolean),
            servicesText: copilotData.services && copilotData.services.length > 0 ? copilotData.services.join('. ') : (cleanServices || input.servicesText),
            timings: copilotData.timings || input.hoursText,
            urgentNotice: copilotData.urgentNotice,
            address: copilotData.address,
            phoneNumbers: copilotData.phoneNumbers && copilotData.phoneNumbers.length > 0 ? copilotData.phoneNumbers : [],
            spokenSentence: copilotData.spokenSentence,
            keyFacts: extraContext.keyDetails,
            actionableTip: extraContext.guidanceTip
          };

          // Post-verification safety guard: enforce local stripping of any residual statutory codes or gibberish
          let finalSpoken = sanitizeGibberishAndCodes(copilotData.spokenSentence);

          // Language-Fidelity Guard:
          // 1. Devanagari regional languages (Maithili, Sanskrit, Bodo, Dogri, Konkani, Nepali) where LLMs default to Hindi verbs ('है', 'हैं')
          // 2. Santali (sat): Check if output lacks authentic Ol Chiki script characters (\u1C50-\u1C7F)
          // 3. Manipuri (mni): Check if output lacks authentic Meitei Mayek characters (\uABC0-\uABFF)
          // 4. Kashmiri & Sindhi (ks, sd): Check if output lacks authentic Perso-Arabic characters (\u0600-\u06FF)
          // 5. Odia (or): Check if output lacks Odia script characters (\u0B00-\u0B7F)
          // 6. Assamese (as): Check if output lacks Assamese/Bengali characters (\u0980-\u09FF)
          const isDevanagariNonHindi = ['mai', 'sa', 'kok', 'doi', 'ne', 'brx'].includes(targetLang);
          const hasHindiVerbEnding = /\b(है|हैं|था|थी|थे|रहा\s*है|रही\s*है)\b/u.test(finalSpoken);
          const isMissingOlChiki = targetLang === 'sat' && !/[\u1C50-\u1C7F]/.test(finalSpoken);
          const isMissingMeitei = targetLang === 'mni' && !/[\uABC0-\uABFF]/.test(finalSpoken);
          const isMissingArabicScript = ['ks', 'sd', 'ur'].includes(targetLang) && !/[\u0600-\u06FF]/.test(finalSpoken);
          const isMissingOdia = targetLang === 'or' && !/[\u0B00-\u0B7F]/.test(finalSpoken);
          const isMissingAssamese = targetLang === 'as' && !/[\u0980-\u09FF]/.test(finalSpoken);

          if (
            (isDevanagariNonHindi && hasHindiVerbEnding) ||
            isMissingOlChiki ||
            isMissingMeitei ||
            isMissingArabicScript ||
            isMissingOdia ||
            isMissingAssamese
          ) {
            console.warn(`[Gemma Language Guard] Detected inaccurate LLM script for ${langObj.name} (${targetLang}). Rerouting through Bodhan IndicTrans2 pivot...`);
            try {
              const authenticTranslation = await performBodhanTranslate(finalSpoken, targetLang);
              if (authenticTranslation && authenticTranslation.trim().length > 0) {
                finalSpoken = authenticTranslation.trim();
              }
            } catch (transErr) {
              console.warn('[Gemma Language Guard] Bodhan reroute failed, keeping cleaned LLM script:', transErr);
            }
          }

          return {
            synthesizedScript: finalSpoken || copilotData.spokenSentence,
            rawConcatenation,
            source: provider,
            modelUsed: defaultModel,
            domain: detectedArch === 'STREET_WAYFINDING' ? 'ROAD_HIGHWAY' : detectedArch === 'TRANSIT_REGULATORY' ? 'RAILWAY_TRANSIT' : 'COMMERCIAL',
            extraContext,
            identification,
            processingTimeMs: Math.round(performance.now() - startTime)
          };
        }
      } else {
        console.warn(`[Gemma API] HTTP ${res.status}: falling back to built-in multilingual engine`);
      }
    } catch (err) {
      console.warn('[Gemma API] Call failed, falling back to built-in multilingual engine:', err);
    }
  }

  // Built-in intelligent Multilingual Gemma Engine (Local CPU / Zero-Setup)
  const synthesized = synthesizeMultilingualWithLocalGemma(input, domain);

  return {
    synthesizedScript: synthesized,
    rawConcatenation,
    source: 'builtin',
    modelUsed: `Gemma Conversational Engine (${langObj.name})`,
    domain,
    extraContext,
    processingTimeMs: Math.round(performance.now() - startTime)
  };
}

/**
 * Backwards-compatible alias for existing code calling summarizeWithGemmaHindi
 */
export async function summarizeWithGemmaHindi(
  input: { brandName: string; servicesText: string; directionGuide?: string; hoursText?: string },
  settings: AppSettings
) {
  return summarizeWithGemma({ ...input, targetLanguage: 'hi' }, settings);
}
