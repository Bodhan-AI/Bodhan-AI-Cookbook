// File: src/assets/sampleSigns.ts
import { BoundingBox } from '../models/SignboardTypes';

export interface SampleSign {
  id: string;
  name: string;
  sourceLanguage: string;
  sourceLanguageName: string;
  description: string;
  imageUrl: string;
  dimensions: { width: number; height: number };
  rawBlocks: BoundingBox[];
  mockTransliteration: Record<string, string>;
  mockTranslation: Record<string, string>;
}

// Generate realistic standalone SVG data URIs for sample signboards
function createSampleSignSvg(
  bgColor: string,
  borderColor: string,
  title: string,
  subtitle: string,
  phone: string,
  license: string,
  arrow: string,
  hours?: string
): string {
  const hoursText = hours || 'समय: प्रातः 9:00 से रात्रि 9:00 बजे तक (रविवार खुला)';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 800 600">
    <rect width="800" height="600" fill="${bgColor}" rx="12" stroke="${borderColor}" stroke-width="8"/>
    <rect x="20" y="20" width="760" height="80" fill="rgba(0,0,0,0.22)" rx="8"/>
    <text x="40" y="65" font-family="'Noto Sans', 'Segoe UI', Arial, sans-serif" font-size="22" fill="#ffffff" font-weight="bold">${license}</text>
    <text x="760" y="65" text-anchor="end" font-family="'Noto Sans', 'Segoe UI', Arial, sans-serif" font-size="26" fill="#facc15" font-weight="bold">${arrow} ${phone}</text>
    
    <!-- Main Title -->
    <rect x="30" y="130" width="740" height="180" fill="rgba(255,255,255,0.96)" rx="10"/>
    <text x="400" y="240" text-anchor="middle" font-family="'Noto Sans', 'Poppins', 'Segoe UI', sans-serif" font-size="52" fill="#0f172a" font-weight="900">${title}</text>
    
    <!-- Subtitle / Services -->
    <rect x="30" y="340" width="740" height="220" fill="rgba(255,255,255,0.92)" rx="10"/>
    <text x="400" y="420" text-anchor="middle" font-family="'Noto Sans', 'Poppins', 'Segoe UI', sans-serif" font-size="30" fill="#065f46" font-weight="bold">${subtitle}</text>
    <text x="400" y="490" text-anchor="middle" font-family="'Noto Sans', 'Poppins', 'Segoe UI', sans-serif" font-size="24" fill="#334155">${hoursText}</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

export const SAMPLE_SIGNS: SampleSign[] = [
  // 1. Odia Clinic
  {
    id: 'sample-odia-clinic',
    name: 'ଅଗ୍ରୱାଲ କ୍ଲିନିକ (Agarwal Clinic)',
    sourceLanguage: 'or',
    sourceLanguageName: 'Odia',
    description: 'Bhubaneswar clinic sign with right arrow, pediatric services, and phone',
    imageUrl: createSampleSignSvg(
      '#0284c7',
      '#0369a1',
      'ଅଗ୍ରୱାଲ କ୍ଲିନିକ',
      'ଶିଶୁ ରୋଗ ବିଶେଷଜ୍ଞ • ରକ୍ତ ପରୀକ୍ଷା • ଔଷଧ ଦୋକାନ',
      '9820012345',
      'Reg. No: BBSR-MED-84920',
      '→',
      'ସମୟ: ସକାଳ ୯:୦୦ ରୁ ରାତି ୯:୦୦ ପର୍ଯ୍ୟନ୍ତ'
    ),
    dimensions: { width: 800, height: 600 },
    rawBlocks: [
      {
        bbox_xyxy: [40, 30, 300, 80],
        text: 'Reg. No: BBSR-MED-84920',
        type: 'Text'
      },
      {
        bbox_xyxy: [500, 30, 760, 80],
        text: '→ 9820012345',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 150, 750, 290],
        text: 'ଅଗ୍ରୱାଲ କ୍ଲିନିକ',
        type: 'Title'
      },
      {
        bbox_xyxy: [50, 360, 750, 460],
        text: 'ଶିଶୁ ରୋଗ ବିଶେଷଜ୍ଞ. ରକ୍ତ ପରୀକ୍ଷା. ଔଷଧ ଦୋକାନ.',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 480, 750, 540],
        text: 'ସମୟ: ସକାଳ ୯:୦୦ ରୁ ରାତି ୯:୦୦ ପର୍ଯ୍ୟନ୍ତ',
        type: 'Text'
      }
    ],
    mockTransliteration: {
      hi: 'अग्रवाल क्लिनिक',
      en: 'Agarwal Clinic',
      ta: 'அகர்வால் கிளினிக்',
      te: 'అగర్వాల్ క్లినిక్',
      bn: 'আগরওয়াল ক্লিনিক',
      mr: 'अग्रवाल क्लिनिक',
      gu: 'અગ્રવાલ ક્લિનિક',
      kn: 'ಅಗರವಾಲ್ ಕ್ಲಿನಿಕ್',
      or: 'ଅଗ୍ରୱାଲ କ୍ଲିନିକ'
    },
    mockTranslation: {
      hi: 'बाल रोग विशेषज्ञ. रक्त परीक्षण. दवा दुकान. समय: सुबह 9:00 से रात 9:00 बजे तक.',
      en: 'Pediatrician specialist. Blood testing. Medical pharmacy. Hours: 9:00 AM to 9:00 PM.',
      ta: 'குழந்தை நல சிறப்பு மருத்துவர். ரத்தப் பரிசோதனை. மருந்தகம். நேரம்: காலை 9:00 முதல் இரவு 9:00 வரை.',
      te: 'పిల్లల వైద్య నిపుణులు. రక్త పరీక్షలు. మందుల దుకాణం. సమయం: ఉదయం 9:00 నుండి రాత్రి 9:00 వరకు.',
      bn: 'শিশু রোগ বিশেষজ্ঞ. রক্ত পরীক্ষা. ওষুধের দোকান. সময়: সকাল ৯:০০ থেকে রাত ৯:০০ পর্যন্ত.',
      mr: 'बालरोग तज्ज्ञ. रक्त तपासणी. औषध दुकान. वेळ: सकाळी ९:०० ते रात्री ९:००.',
      gu: 'બાળરોગ નિષ્ણાત. લોહીની તપાસ. દવાની દુકાન. સમય: સવારે ૯:૦૦ થી રાત્રે ૯:૦૦.',
      kn: 'ಮಕ್ಕಳ ತಜ್ಞ ವೈದ್ಯರು. ರಕ್ತ ಪರೀಕ್ಷೆ. ಔಷಧಾಲಯ. ಸಮಯ: ಬೆಳಿಗ್ಗೆ 9:00 ರಿಂದ ರಾತ್ರಿ 9:00 ರವರೆಗೆ.'
    }
  },

  // 2. Tamil Sweets
  {
    id: 'sample-tamil-sweets',
    name: 'ஶ்ரீ கிருஷ்ணா ஸ்வீட்ஸ் (Sri Krishna Sweets)',
    sourceLanguage: 'ta',
    sourceLanguageName: 'Tamil',
    description: 'Chennai sweet shop with GSTIN, left arrow, and pure ghee sweets',
    imageUrl: createSampleSignSvg(
      '#b91c1c',
      '#991b1b',
      'ஶ்ரீ கிருஷ்ணா ஸ்வீட்ஸ்',
      'சுத்தமான நெய் இனிப்புகள் • கார வகைகள் • காபி',
      '9444123456',
      'GSTIN: 33AAAAA0000A1Z5',
      '←',
      'நேரம்: காலை 7:00 முதல் இரவு 10:00 வரை'
    ),
    dimensions: { width: 800, height: 600 },
    rawBlocks: [
      {
        bbox_xyxy: [40, 30, 350, 80],
        text: 'GSTIN: 33AAAAA0000A1Z5',
        type: 'Text'
      },
      {
        bbox_xyxy: [500, 30, 760, 80],
        text: '← 9444123456',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 150, 750, 290],
        text: 'ஶ்ரீ கிருஷ்ணா ஸ்வீட்ஸ்',
        type: 'Title'
      },
      {
        bbox_xyxy: [50, 360, 750, 460],
        text: 'சுத்தமான நெய் இனிப்புகள். கார வகைகள். சூடான பில்டர் காபி.',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 480, 750, 540],
        text: 'நேரம்: காலை 7:00 முதல் இரவு 10:00 வரை',
        type: 'Text'
      }
    ],
    mockTransliteration: {
      hi: 'श्री कृष्णा स्वीट्स',
      en: 'Sri Krishna Sweets',
      te: 'శ్రీ కృష్ణ స్వీట్స్',
      ta: 'ஶ்ரீ கிருஷ்ணா ஸ்வீட்ஸ்',
      bn: 'শ্রী কৃষ্ণা সুইটস',
      mr: 'श्री कृष्णा स्वीट्स',
      gu: 'શ્રી ક્રિષ્ના સ્વીટ્સ',
      kn: 'ಶ್ರೀ ಕೃಷ್ಣ ಸ್ವೀಟ್ಸ್'
    },
    mockTranslation: {
      hi: 'शुद्ध घी की मिठाइयां. नमकीन एवं स्नैक्स. गरम फ़िल्टर कॉफ़ी. समय: सुबह 7:00 से रात 10:00 बजे तक.',
      en: 'Pure ghee sweets. Savory snacks. Hot filter coffee. Hours: 7:00 AM to 10:00 PM.',
      te: 'స్వచ్ఛమైన నెయ్యి మిఠాయిలు. కారపు తినుబండారాలు. వేడి ఫిల్టర్ కాఫీ. సమయం: ఉదయం 7:00 నుండి రాత్రి 10:00 వరకు.',
      bn: 'খাঁটি ঘিয়ে ভাজা মিষ্টি. নোনতা স্ন্যাক্স. গরম ফিল্টার কফি. সময়: সকাল ৭:০০ থেকে রাত ১০:০০.',
      mr: 'शुद्ध तुपातील मिठाई. फरसाण व स्नॅक्स. गरमागरम फिल्टर कॉफी. वेळ: सकाळी ७:०० ते रात्री १०:००.',
      gu: 'શુદ્ધ ઘીની મીઠાઈઓ. નમકીન અને ફરસાણ. ગરમ ફિલ્ટર કોફી. સમય: સવારે ૭:૦૦ થી રાત્રે ૧૦:૦૦.',
      kn: 'ಶುದ್ಧ ತುಪ್ಪದ ಸಿಹಿ ತಿಂಡಿಗಳು. ಖಾರದ ತಿನಿಸುಗಳು. ಬಿಸಿ ಫಿಲ್ಟರ್ ಕಾಫಿ. ಸಮಯ: ಬೆಳಿಗ್ಗೆ 7:00 ರಿಂದ ರಾತ್ರಿ 10:00.'
    }
  },

  // 3. Hindi Opticals
  {
    id: 'sample-hindi-opticals',
    name: 'शर्मा ऑप्टिकल्स एवं नेत्र जांच (Sharma Opticals)',
    sourceLanguage: 'hi',
    sourceLanguageName: 'Hindi',
    description: 'Jaipur optometry store with forward arrow, eye test and designer frames',
    imageUrl: createSampleSignSvg(
      '#0f766e',
      '#115e59',
      'शर्मा ऑप्टिकल्स',
      'कंप्यूटरीकृत नेत्र जांच • चश्मे के फ्रेम • कॉन्टैक्ट लेंस',
      '9829011223',
      'Shop Act Reg: RJ-JPR-2021-9921',
      '▲',
      'समय: सुबह 10:00 से रात 8:30 बजे तक'
    ),
    dimensions: { width: 800, height: 600 },
    rawBlocks: [
      {
        bbox_xyxy: [40, 30, 380, 80],
        text: 'Shop Act Reg: RJ-JPR-2021-9921',
        type: 'Text'
      },
      {
        bbox_xyxy: [520, 30, 760, 80],
        text: '▲ 9829011223',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 150, 750, 290],
        text: 'शर्मा ऑप्टिकल्स',
        type: 'Title'
      },
      {
        bbox_xyxy: [50, 360, 750, 460],
        text: 'कंप्यूटरीकृत नेत्र जांच. डिजाइनर चश्मे के फ्रेम. कॉन्टैक्ट लेंस.',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 480, 750, 540],
        text: 'समय: सुबह 10:00 से रात 8:30 बजे तक',
        type: 'Text'
      }
    ],
    mockTransliteration: {
      hi: 'शर्मा ऑप्टिकल्स',
      en: 'Sharma Opticals',
      ta: 'சர்மா ஆப்டிகல்ஸ்',
      te: 'శర్మ ఆప్టికల్స్',
      bn: 'শর্মা অপটিক্যালস',
      mr: 'शर्मा ऑप्टिकल्स',
      gu: 'શર્મા ઓપ્ટિકલ્સ',
      kn: 'ಶರ್ಮಾ ಆಪ್ಟಿಕಲ್ಸ್'
    },
    mockTranslation: {
      en: 'Computerized eye testing. Designer spectacle frames. Contact lenses. Hours: 10:00 AM to 8:30 PM.',
      ta: 'கணினிமயமாக்கப்பட்ட கண் பரிசோதனை. பிரேம்கள். காண்டாக்ட் லென்ஸ்கள். நேரம்: காலை 10:00 முதல் இரவு 8:30 வரை.',
      te: 'కంప్యూటరైజ్డ్ కంటి పరీక్ష. డిజైనర్ కళ్ళజోడు ఫ్రేమ్‌లు. కాంటాక్ట్ లెన్సులు. సమయం: ఉదయం 10:00 నుండి రాత్రి 8:30 వరకు.',
      bn: 'কম্পিউটারাইজড চোখের পরীক্ষা. ডিজাইনার ফ্রেম. কন্টাক্ট লেন্স. সময়: সকাল ১০:০০ থেকে রাত ৮:৩০.',
      mr: 'संगणकीकृत डोळे तपासणी. डिझायनर चष्मा फ्रेम्स. कॉन्टॅक्ट लेन्स. वेळ: सकाळी १०:०० ते रात्री ८:३०.',
      gu: 'કમ્પ્યુટરાઇઝ્ડ આંખની તપાસ. ડિઝાઇનર ચશ્મા ફ્રેમ્સ. કોન્ટેક્ટ લેન્સ. સમય: સવારે ૧૦:૦૦ થી રાત્રે ૮:૩૦.',
      kn: 'ಕಂಪ್ಯೂಟರೀಕೃತ ಕಣ್ಣಿನ ತಪಾಸಣೆ. ಡಿಸೈನರ್ ಕನ್ನಡಕ ಫ್ರೇಮ್‌ಗಳು. ಕಾಂಟ್ಯಾಕ್ಟ್ ಲೆನ್ಸ್. ಸಮಯ: ಬೆಳಿಗ್ಗೆ 10:00 ರಿಂದ ರಾತ್ರಿ 8:30.'
    }
  },

  // 4. Bengali Book Depot (Kolkata College Street)
  {
    id: 'sample-bengali-books',
    name: 'দাসগুপ্ত বুক ডিপো (Dasgupta Book Depot)',
    sourceLanguage: 'bn',
    sourceLanguageName: 'Bengali',
    description: 'Kolkata College Street bookstore with textbooks, stationery, and old book buy/sell',
    imageUrl: createSampleSignSvg(
      '#7c2d12',
      '#9a3412',
      'দাসগুপ্ত বুক ডিপো',
      'স্কুল ও কলেজ পাঠ্যবই • খাতা ও স্টেশনারি • পুরোনো বই কেনাবেচা',
      '9831098765',
      'Trade Lic: KMC-COL-4912',
      '→',
      'সময়: সকাল ১০:০০ থেকে রাত ৮:৩০ পর্যন্ত'
    ),
    dimensions: { width: 800, height: 600 },
    rawBlocks: [
      {
        bbox_xyxy: [40, 30, 340, 80],
        text: 'Trade Lic: KMC-COL-4912',
        type: 'Text'
      },
      {
        bbox_xyxy: [500, 30, 760, 80],
        text: '→ 9831098765',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 150, 750, 290],
        text: 'দাসগুপ্ত বুক ডিপো',
        type: 'Title'
      },
      {
        bbox_xyxy: [50, 360, 750, 460],
        text: 'স্কুল ও কলেজ পাঠ্যবই. খাতা ও স্টেশনারি সামগ্রী. পুরোনো বই কেনাবেচা.',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 480, 750, 540],
        text: 'সময়: সকাল ১০:০০ থেকে রাত ৮:৩০ পর্যন্ত',
        type: 'Text'
      }
    ],
    mockTransliteration: {
      en: 'Dasgupta Book Depot',
      hi: 'दासगुप्ता बुक डिपो',
      bn: 'দাসগুপ্ত বুক ডিপো',
      ta: 'தாஸ்குப்தா புக் டிப்போ',
      te: 'దాస్‌గుప్తా బుక్ డిపో',
      mr: 'दासगुप्ता बुक डेपो'
    },
    mockTranslation: {
      en: 'School and college textbooks. Notebooks and stationery. Buying and selling old books. Hours: 10:00 AM to 8:30 PM.',
      hi: 'स्कूल एवं कॉलेज की पाठ्यपुस्तकें. नोटबुक एवं स्टेशनरी. पुरानी पुस्तकों की खरीद-बिक्री. समय: सुबह 10:00 से रात 8:30 तक.',
      ta: 'பள்ளி மற்றும் கல்லூரி பாடப்புத்தகங்கள். நோட்டுப்புத்தகங்கள் மற்றும் எழுதுபொருட்கள். பழைய புத்தகங்கள் வாங்க/விற்க.',
      te: 'స్కూల్ మరియు కాలేజీ పాఠ్యపుస్తకాలు. నోట్‌బుక్‌లు మరియు స్టేషనరీ. పాత పుస్తకాల కొనుగోలు మరియు అమ్మకం.',
      mr: 'शालेय व महाविद्यालयीन पाठ्यपुस्तके. वह्या व स्टेशनरी. जुनी पुस्तके खरेदी-विक्री.'
    }
  },

  // 5. Marathi Pathology Lab (Pune FC Road)
  {
    id: 'sample-marathi-lab',
    name: 'सह्याद्री पॅथॉलॉजी लॅब (Sahyadri Pathology Lab)',
    sourceLanguage: 'mr',
    sourceLanguageName: 'Marathi',
    description: 'Pune medical laboratory with NABL accreditation, blood tests, and home collection',
    imageUrl: createSampleSignSvg(
      '#4338ca',
      '#3730a3',
      'सह्याद्री पॅथॉलॉजी लॅब',
      'रक्त लघवी तपासणी • थायरॉईड टेस्ट • घरपोच सॅम्पल कलेक्शन',
      '9822055667',
      'NABL Accredited: MC-2910',
      '←',
      'वेळ: सकाळी ७:०० ते रात्री ९:०० पर्यंत'
    ),
    dimensions: { width: 800, height: 600 },
    rawBlocks: [
      {
        bbox_xyxy: [40, 30, 350, 80],
        text: 'NABL Accredited: MC-2910',
        type: 'Text'
      },
      {
        bbox_xyxy: [500, 30, 760, 80],
        text: '← 9822055667',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 150, 750, 290],
        text: 'सह्याद्री पॅथॉलॉजी लॅब',
        type: 'Title'
      },
      {
        bbox_xyxy: [50, 360, 750, 460],
        text: 'रक्त व लघवी तपासणी. थायरॉईड प्रोफाइल. मोफत घरपोच सॅम्पल कलेक्शन.',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 480, 750, 540],
        text: 'वेळ: सकाळी ७:०० ते रात्री ९:०० पर्यंत',
        type: 'Text'
      }
    ],
    mockTransliteration: {
      en: 'Sahyadri Pathology Lab',
      hi: 'सह्याद्री पैथोलॉजी लैब',
      mr: 'सह्याद्री पॅथॉलॉजी लॅब',
      gu: 'સહ્યાદ્રિ પેથોલોજી લેબ',
      kn: 'ಸಹ್ಯಾದ್ರಿ ಪ್ಯಾಥಾಲಜಿ ಲ್ಯಾಬ್'
    },
    mockTranslation: {
      en: 'Blood and urine testing. Thyroid profile. Free home sample collection. Hours: 7:00 AM to 9:00 PM.',
      hi: 'रक्त एवं मूत्र परीक्षण. थायरॉयड प्रोफाइल. निःशुल्क होम सैंपल कलेक्शन. समय: सुबह 7:00 से रात 9:00 बजे तक.',
      gu: 'લોહી અને પેશાબની તપાસ. થાઇરોઇડ પ્રોફાઇલ. મફત ઘરબેઠા સેમ્પલ કલેક્શન.',
      kn: 'ರಕ್ತ ಮತ್ತು ಮೂತ್ರ ಪರೀಕ್ಷೆ. ಥೈರಾಯ್ಡ್ ಪ್ರೊಫೈಲ್. ಉಚಿತ ಮನೆ ಬಾಗಿಲಿಗೆ ಮಾದರಿ ಸಂಗ್ರಹಣೆ.'
    }
  },

  // 6. Telugu Mobile Store (Hyderabad Ameerpet)
  {
    id: 'sample-telugu-mobiles',
    name: 'శ్రీ వెంకటేశ్వర మొబైల్స్ (Sri Venkateshwara Mobiles)',
    sourceLanguage: 'te',
    sourceLanguageName: 'Telugu',
    description: 'Hyderabad electronics shop with smartphones, repair, and accessories',
    imageUrl: createSampleSignSvg(
      '#15803d',
      '#166534',
      'శ్రీ వెంకటేశ్వర మొబైల్స్',
      'స్మార్ట్‌ఫోన్లు • మొబైల్ రిపేర్ • ఒరిజినల్ యాక్సెసరీస్',
      '9848011223',
      'GSTIN: 36ABCDE1234F1Z8',
      '→',
      'సమయం: ఉదయం 10:00 నుండి రాత్రి 10:00 వరకు'
    ),
    dimensions: { width: 800, height: 600 },
    rawBlocks: [
      {
        bbox_xyxy: [40, 30, 360, 80],
        text: 'GSTIN: 36ABCDE1234F1Z8',
        type: 'Text'
      },
      {
        bbox_xyxy: [500, 30, 760, 80],
        text: '→ 9848011223',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 150, 750, 290],
        text: 'శ్రీ వెంకటేశ్వర మొబైల్స్',
        type: 'Title'
      },
      {
        bbox_xyxy: [50, 360, 750, 460],
        text: 'అన్ని బ్రాండ్ల స్మార్ట్‌ఫోన్లు. క్విక్ మొబైల్ రిపేర్. ఒరిజినల్ యాక్సెసరీస్. స్క్రీన్ రీప్లేస్‌మెంట్.',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 480, 750, 540],
        text: 'సమయం: ఉదయం 10:00 నుండి రాత్రి 10:00 వరకు',
        type: 'Text'
      }
    ],
    mockTransliteration: {
      en: 'Sri Venkateshwara Mobiles',
      hi: 'श्री वेंकटेश्वर मोबाइल्स',
      te: 'శ్రీ వెంకటేశ్వర మొబైల్స్',
      ta: 'ஸ்ரீ வெங்கடேஸ்வரா மொபைல்ஸ்',
      kn: 'ಶ್ರೀ ವೆಂಕಟೇಶ್ವರ ಮೊಬೈಲ್ಸ್'
    },
    mockTranslation: {
      en: 'All smartphone brands. Quick mobile repairs. Genuine accessories. Screen replacement. Hours: 10:00 AM to 10:00 PM.',
      hi: 'सभी ब्रांड के स्मार्टफोन. त्वरित मोबाइल रिपेयरिंग. ओरिजिनल एक्सेसरीज. स्क्रीन रिप्लेसमेंट. समय: सुबह 10:00 से रात 10:00 तक.',
      ta: 'அனைத்து பிராண்ட் ஸ்மார்ட்போன்கள். விரைவான மொபைல் பழுதுநீக்கம். அசல் உபகரணங்கள். திரை மாற்றுதல்.',
      kn: 'ಎಲ್ಲಾ ಬ್ರಾಂಡ್‌ಗಳ ಸ್ಮಾರ್ಟ್‌ಫೋನ್‌ಗಳು. ತ್ವರಿತ ಮೊಬೈಲ್ ರಿಪೇರಿ. ಅಸಲಿ ಆಕ್ಸೆಸರೀಸ್. ಸ್ಕ್ರೀನ್ ಬದಲಿ.'
    }
  },

  // 7. Gujarati Jewellers (Ahmedabad Manek Chowk)
  {
    id: 'sample-gujarati-jewellers',
    name: 'પટેલ જ્વેલર્સ (Patel Jewellers)',
    sourceLanguage: 'gu',
    sourceLanguageName: 'Gujarati',
    description: 'Ahmedabad jewellery showroom with 916 BIS hallmark gold and silver ornaments',
    imageUrl: createSampleSignSvg(
      '#831843',
      '#701a75',
      'પટેલ જ્વેલર્સ',
      '૯૧૬ હોલમાર્ક સોનાના દાગીના • ચાંદીના વાસણો • ડાયમંડ જ્વેલરી',
      '9825044332',
      'BIS Hallmarked: HM-GJ-9182',
      '▲',
      'સમય: સવારે ૧૧:૦૦ થી રાત્રે ૮:૩૦ (સોમવાર બંધ)'
    ),
    dimensions: { width: 800, height: 600 },
    rawBlocks: [
      {
        bbox_xyxy: [40, 30, 360, 80],
        text: 'BIS Hallmarked: HM-GJ-9182',
        type: 'Text'
      },
      {
        bbox_xyxy: [500, 30, 760, 80],
        text: '▲ 9825044332',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 150, 750, 290],
        text: 'પટેલ જ્વેલર્સ',
        type: 'Title'
      },
      {
        bbox_xyxy: [50, 360, 750, 460],
        text: '૯૧૬ હોલમાર્ક સોનાના દાગીના. ચાંદીના વાસણો અને સિક્કા. આકર્ષક ડાયમંડ જ્વેલરી.',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 480, 750, 540],
        text: 'સમય: સવારે ૧૧:૦૦ થી રાત્રે ૮:૩૦ (સોમવાર બંધ)',
        type: 'Text'
      }
    ],
    mockTransliteration: {
      en: 'Patel Jewellers',
      hi: 'पटेल ज्वेलर्स',
      gu: 'પટેલ જ્વેલર્સ',
      mr: 'पटेल ज्वेलर्स',
      ta: 'படேல் ஜூவல்லர்ஸ்'
    },
    mockTranslation: {
      en: '916 Hallmarked gold ornaments. Silver utensils and coins. Designer diamond jewellery. Hours: 11:00 AM to 8:30 PM (Closed Monday).',
      hi: '916 हॉलमार्क सोने के आभूषण. चांदी के बर्तन एवं सिक्के. आकर्षक डायमंड ज्वेलरी. समय: सुबह 11:00 से रात 8:30 (सोमवार बंद).',
      mr: '९१६ हॉलमार्क सोन्याचे दागिने. चांदीची भांडी व नाणी. डिझायनर डायमंड ज्वेलरी.',
      ta: '916 ஹால்மார்க் தங்க நகைகள். வெள்ளி பாத்திரங்கள் மற்றும் நாணயங்கள். வைர நகைகள்.'
    }
  },

  // 8. Kannada Tiffin House (Bengaluru Malleshwaram)
  {
    id: 'sample-kannada-bhavan',
    name: 'ಉಡುಪಿ ಶ್ರೀ ಕೃಷ್ಣ ಭವನ (Udupi Sri Krishna Bhavan)',
    sourceLanguage: 'kn',
    sourceLanguageName: 'Kannada',
    description: 'Bengaluru traditional tiffin restaurant with idli, dose, and filter coffee',
    imageUrl: createSampleSignSvg(
      '#c2410c',
      '#9a3412',
      'ಉಡುಪಿ ಶ್ರೀ ಕೃಷ್ಣ ಭವನ',
      'ಮಲ್ಲಿಗೆ ಇಡ್ಲಿ • ಬೆಣ್ಣೆ ದೋಸೆ • ಬಿಸಿ ಫಿಲ್ಟರ್ ಕಾಫಿ',
      '9845012340',
      'FSSAI Lic No: 11218334000542',
      '←',
      'ಸಮಯ: ಬೆಳಿಗ್ಗೆ 6:30 ರಿಂದ ರಾತ್ರಿ 10:00 ರವರೆಗೆ'
    ),
    dimensions: { width: 800, height: 600 },
    rawBlocks: [
      {
        bbox_xyxy: [40, 30, 380, 80],
        text: 'FSSAI Lic No: 11218334000542',
        type: 'Text'
      },
      {
        bbox_xyxy: [500, 30, 760, 80],
        text: '← 9845012340',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 150, 750, 290],
        text: 'ಉಡುಪಿ ಶ್ರೀ ಕೃಷ್ಣ ಭವನ',
        type: 'Title'
      },
      {
        bbox_xyxy: [50, 360, 750, 460],
        text: 'ಮಲ್ಲಿಗೆ ಇಡ್ಲಿ. ಗರಿಗರಿ ಬೆಣ್ಣೆ ದೋಸೆ. ಬಿಸಿ ಫಿಲ್ಟರ್ ಕಾಫಿ. ಮಂಗಳೂರು ಬನ್ಸ್.',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 480, 750, 540],
        text: 'ಸಮಯ: ಬೆಳಿಗ್ಗೆ 6:30 ರಿಂದ ರಾತ್ರಿ 10:00 ರವರೆಗೆ',
        type: 'Text'
      }
    ],
    mockTransliteration: {
      en: 'Udupi Sri Krishna Bhavan',
      hi: 'उडुपी श्री कृष्ण भवन',
      kn: 'ಉಡುಪಿ ಶ್ರೀ ಕೃಷ್ಣ ಭವನ',
      ta: 'உடுப்பி ஸ்ரீ கிருஷ்ணா பவன்',
      te: 'ఉడుపి శ్రీ కృష్ణ భవన్'
    },
    mockTranslation: {
      en: 'Soft mallige idli. Crispy butter dose. Hot filter coffee. Mangalore buns. Hours: 6:30 AM to 10:00 PM.',
      hi: 'मखमली इडली. क्रिस्पी बटर डोसा. गरम फ़िल्टर कॉफ़ी. मैंगलोर बन्स. समय: सुबह 6:30 से रात 10:00 तक.',
      ta: 'மல்லிகை இட்லி. நெய் தோசை. சூடான பில்டர் காபி. மங்களூர் பன்ஸ்.',
      te: 'మల్లెపూల ఇడ్లీ. క్రిస్పీ బటర్ దోశ. వేడి ఫిల్ಟರ್ కాఫీ. మంగళూరు బన్స్.'
    }
  },

  // 9. National Highway Road Sign (Road & Wayfinding)
  {
    id: 'sample-highway-road-sign',
    name: 'ರಾಷ್ಟ್ರೀಯ ಹೆದ್ದಾರಿ NH-48 (National Highway 48)',
    sourceLanguage: 'kn',
    sourceLanguageName: 'Road Sign (Kannada & English)',
    description: 'Highway wayfinding board: NH-48 Pune 120 km, Toll Plaza 500m, Speed Limit 80',
    imageUrl: createSampleSignSvg(
      '#065f46',
      '#047857',
      'NH-48 • ಪುಣೆ 120 km',
      'ಮುಂಬೈ ಕಡೆಗೆ ನೇರವಾಗಿ ಸಾಗಿ • ಟೋಲ್ ಪ್ಲಾಜಾ 500m • ವೇಗದ ಮಿತಿ 80',
      'NHAI Toll 1033',
      'National Highway 48',
      '▲',
      'ವೇಗದ ಮಿತಿ: ಗರಿಷ್ಠ 80 km/h • ಎಡ ಲೇನ್‌ನಲ್ಲಿ ಚಲಿಸಿ'
    ),
    dimensions: { width: 800, height: 600 },
    rawBlocks: [
      {
        bbox_xyxy: [40, 30, 340, 80],
        text: 'National Highway 48',
        type: 'Text'
      },
      {
        bbox_xyxy: [500, 30, 760, 80],
        text: '▲ NHAI Toll 1033',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 150, 750, 290],
        text: 'NH-48 ಪುಣೆ 120 km',
        type: 'Title'
      },
      {
        bbox_xyxy: [50, 360, 750, 460],
        text: 'ಮುಂಬೈ ಕಡೆಗೆ ನೇರವಾಗಿ ಸಾಗಿ. ಟೋಲ್ ಪ್ಲಾಜಾ 500m. ವೇಗದ ಮಿತಿ 80.',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 480, 750, 540],
        text: 'ಎಡ ಲೇನ್‌ನಲ್ಲಿ ಚಲಿಸಿ. Speed limit 80 km/h.',
        type: 'Text'
      }
    ],
    mockTransliteration: {
      en: 'NH-48 Pune 120 km',
      hi: 'एनएच-48 पुणे 120 किमी',
      kn: 'NH-48 ಪುಣೆ 120 km',
      ta: 'என்.எச்-48 புனே 120 கி.மீ',
      te: 'ఎన్‌హెచ్-48 పూణే 120 కి.మీ',
      mr: 'एनएच-४८ पुणे १२० किमी',
      gu: 'એનએચ-૪૮ પુણે ૧૨૦ કિમી',
      bn: 'এনএইচ-৪৮ পুনে ১২০ কিমি',
      or: 'ଏନଏଚ-୪୮ ପୁଣେ ୧୨୦ କିମି'
    },
    mockTranslation: {
      en: 'Follow straight for Mumbai. Toll plaza 500m. Speed limit 80 km/h. Keep left lane.',
      hi: 'मुंबई के लिए सीधे चलें. टोल प्लाजा 500 मीटर. गति सीमा 80 किमी/घंटा. बाएं लेन में रहें.',
      ta: 'மும்பைக்கு நேராகச் செல்லவும். சுங்கச்சாவடி 500 மீ. வேக வரம்பு 80 கி.மீ/மணி. இடது பாதையில் செல்லவும்.',
      te: 'ముంబై వైపు నేరుగా వెళ్ళండి. టోల్ ప్లాజా 500 మీటర్లు. వేగ పరిమితి 80 కి.మీ/గం. ఎడమ లేన్‌లో ఉండండి.',
      mr: 'मुंबईसाठी सरळ पुढे जा. टोल नाका ५०० मीटर. वेग मर्यादा ८० किमी/तास. डाव्या लेनमध्ये राहा.',
      gu: 'મુંબઈ માટે સીધા આગળ વધો. ટોલ પ્લાઝા ૫૦૦ મીટર. ઝડપ મર્યાદા ૮૦ કિમી/કલાક. ડાબી લેનમાં રહો.',
      bn: 'মুম্বাইয়ের জন্য সোজা এগিয়ে যান। টোল প্লাজা ৫০০ মিটার। গতিসীমা ৮০ কিমি/ঘণ্টা। বাম লেনে থাকুন।',
      or: 'ମୁମ୍ବାଇ ପାଇଁ ସିଧା ଆଗକୁ ଯାଆନ୍ତୁ। ଟୋଲ ପ୍ଲାଜା ୫୦୦ ମିଟର। ଗତି ସୀମା ୮୦ କିମି/ଘଣ୍ଟା। ବାମ ଲେନରେ ରୁହନ୍ତୁ।'
    }
  },

  // 10. Railway Station Transit Sign (Platform & Foot Overbridge)
  {
    id: 'sample-railway-transit-sign',
    name: 'प्लेटफॉर्म ३ और ४ (Platform 3 & 4)',
    sourceLanguage: 'hi',
    sourceLanguageName: 'Railway Sign (Hindi & English)',
    description: 'Railway station wayfinding sign: Platform 3 & 4, Foot Overbridge, Ticket Counter',
    imageUrl: createSampleSignSvg(
      '#1e3a8a',
      '#172554',
      'प्लेटफॉर्म ३ और ४ (Platform 3 & 4)',
      'पैदल उपरिगामी पुल (FOB) आगे • प्रतीक्षालय एवं टिकट काउंटर • निकास द्वार २',
      'Helpline 139',
      'Indian Railways',
      '→',
      'लिफ्ट एवं एस्केलेटर की सुविधा उपलब्ध'
    ),
    dimensions: { width: 800, height: 600 },
    rawBlocks: [
      {
        bbox_xyxy: [40, 30, 300, 80],
        text: 'Indian Railways',
        type: 'Text'
      },
      {
        bbox_xyxy: [500, 30, 760, 80],
        text: '→ Helpline 139',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 150, 750, 290],
        text: 'प्लेटफॉर्म ३ और ४ • Platform 3 & 4',
        type: 'Title'
      },
      {
        bbox_xyxy: [50, 360, 750, 460],
        text: 'पैदल उपरिगामी पुल (FOB) आगे. प्रतीक्षालय एवं टिकट काउंटर. निकास द्वार २.',
        type: 'Text'
      },
      {
        bbox_xyxy: [50, 480, 750, 540],
        text: 'लिफ्ट एवं एस्केलेटर की सुविधा उपलब्ध.',
        type: 'Text'
      }
    ],
    mockTransliteration: {
      en: 'Platform 3 and 4',
      hi: 'प्लेटफॉर्म ३ और ४',
      ta: 'நடைமேடை 3 மற்றும் 4',
      te: 'ప్లాట్‌ఫాం 3 మరియు 4',
      kn: 'ಪ್ಲಾಟ್‌ಫಾರ್ಮ್ 3 ಮತ್ತು 4',
      mr: 'प्लॅटफॉर्म ३ आणि ४',
      gu: 'પ્લેટફોર્મ ૩ અને ૪',
      bn: 'প্ল্যাটফর্ম ৩ এবং ৪',
      or: 'ପ୍ଲାଟଫର୍ମ ୩ ଏବଂ ୪'
    },
    mockTranslation: {
      en: 'Foot overbridge ahead. Waiting hall and ticket booking counter. Exit Gate 2. Lift and escalator available.',
      hi: 'पैदल उपरिगामी पुल आगे. प्रतीक्षालय एवं टिकट बुकिंग काउंटर. निकास द्वार 2. लिफ्ट और एस्केलेटर सुविधा उपलब्ध.',
      ta: 'நடைமேம்பாலம் முன்னே உள்ளது. காத்திருப்பு கூடம் மற்றும் பயணச்சீட்டு மையம். வெளியேறும் வாயில் 2. மின்தூக்கி வசதி உண்டு.',
      te: 'ఫుట్ ఓవర్‌బ్రిడ్జి ముందుకు ఉంది. వేచి ఉండే గది మరియు టికెట్ కౌంటర్. ఎగ్జిట్ గేట్ 2. లిఫ్ట్ మరియు ఎస్కలేటర్ సౌకర్యం ఉంది.',
      mr: 'पादचारी पूल पुढे आहे. प्रतीक्षालय व तिकीट काउंटर. बाहेर पडण्याचे द्वार २. लिफ्ट व एस्केलेटर उपलब्ध.',
      gu: 'ફૂટ ઓવરબ્રિજ આગળ છે. વેઇટિંગ હોલ અને ટિકિટ કાઉન્ટર. એક્ઝિટ ગેટ ૨. લિફ્ટ અને એસ્કેલેટર સુવિધા ઉપલબ્ધ.',
      bn: 'সামনে ফুট ওভারব্রিজ। ওয়েটিং হল এবং টিকিট কাউন্টার। প্রস্থান ফটক ২। লিফট এবং এসকেলেটর উপলব্ধ।',
      or: 'ଆଗରେ ପାଦଚଲା ପୋଲ (FOB)। ବିଶ୍ରାମାଗାର ଏବଂ ଟିକେଟ କାଉଣ୍ଟର। ପ୍ରସ୍ଥାନ ଦ୍ୱାର ୨। ଲିଫ୍ଟ ଏବଂ ଏସ୍କାଲେଟର ସୁବିଧା ଉପଲବ୍ଧ।'
    }
  }
];
