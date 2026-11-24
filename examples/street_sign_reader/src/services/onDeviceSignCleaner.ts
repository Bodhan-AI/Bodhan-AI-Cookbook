// File: src/services/onDeviceSignCleaner.ts
import {
  BoundingBox,
  CleanedSignboardResult,
  SemanticBlock,
  SignboardArchetype,
  WayfindingDetails,
  CommercialDetails,
  TransitDetails,
  DestinationDistancePair
} from '../models/SignboardTypes';

// 1. Comprehensive Statutory & Regulatory License Patterns (for shops/commercial only)
const STATUTORY_REGEX =
  /\b(?:FSSAI|Lic\.?(?:ence|ense)?(?:\s*No\.?)?|DL|D\.L\.|Drug\s*Lic\.?(?:\s*No\.?)?|Reg\.?(?:d|n|istration)?(?:\s*No\.?)?|R\.?\s*No\.?|Shop\s*Act|Gumasta|TIN|CIN|PAN|TAN|GST(?:IN)?(?:\s*No\.?)?|Trade\s*Lic\.?(?:\s*No\.?)?|TL\s*No\.?|ISO(?:\s*9001)?|NABL(?:\s*Accredited)?|Udyam|MSME|BIS(?:\s*Hallmark(?:ed)?)?)[.:\s#\/-]*[A-Za-z0-9\/-]+/gi;

// 2. GSTIN (15-character alphanumeric Indian tax identity)
const GSTIN_REGEX = /\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b/gi;

// 3. Drug License specifics (e.g., 20-B, 21-B, 20B/1492/98)
const DRUG_LIC_REGEX = /\b(?:20-?[BC]|21-?[BC])[A-Za-z0-9\/-]*\b/gi;

// 4. Mixed alphanumeric serial/license codes of 8+ characters (e.g. 27AAAAA0000A1Z5, DL20B149298, ABC12345XYZ)
const SERIAL_GIBBERISH_REGEX = /\b(?=[A-Za-z\d]*[A-Za-z])(?=[A-Za-z\d]*\d)[A-Za-z\d]{8,}\b/g;

// 5. Website URLs, Designer Portals, and Domains
const URL_REGEX =
  /(?:https?:\/\/|www\.)[a-z0-9\-]+(?:\.[a-z0-9\-]+)+(?:\/[^\s]*)?|\b[a-z0-9\-]+(?:\.com|\.in|\.org|\.net|\.co\.in|\.gov\.in|\.io|\.ai)\b/gi;

// 6. Graphic Designer flex/banner template boilerplate & printing press watermarks
const DESIGNER_TEMPLATE_REGEX =
  /(?:\b(?:editable\s*with\s*font|corel(?:\s*draw)?(?:\s*cdr)?(?:\s*x\d+)?(?:\s*to\s*\d+)?|cdr(?:\s*x\d+)?(?:\s*to\s*\d+)?|adobe\s*photoshop|photoshop|psd|flex\s*(?:design|print|banner|board)|digital\s*(?:print|banner)|vinyl\s*(?:print|banner)|offset\s*(?:printers?|press)|amangraphics|[a-z0-9\-]+graphics)\b)/gi;

// 7. Dummy / Placeholder Phone Numbers (e.g. 1234567890, 9876543210, 0000000000)
const DUMMY_PHONE_REGEX =
  /\b(?:1234567890|9876543210|0123456789|0000000000|1111111111|1234512345|9999999999|8888888888)\b/g;

// 8. Directional Arrows
const ARROW_REGEX = /[→⇒▶]|->|-->|[←⇐◀]|<-|<--|[↑▲]|\^/g;

// 9. Indian Mobile & Landline Phone Numbers (valid 10-digit starting 6-9, 5+5 digit with spaces, and STD landlines)
const PHONE_REGEX =
  /(?:\+91[\-\s]?)?[6-9]\d{4}[\s\-\.]?\d{5}\b|(?:\+91[\-\s]?)?[6-9]\d{9}\b|0\d{2,4}[\-\s]?\d{6,8}\b/g;

// 10. Landmark and Street Address Anchors (Bilingual Hindi/Indic and English)
const ADDRESS_ANCHOR_REGEX =
  /(?:(?:बैंक|मंदिर|मन्दिर|हॉस्पिटल|अस्पताल|स्कूल|कॉलेज|पेट्रोल\s*पम्प|थाना|चौक|स्टेशन|बस\s*स्टैंड|पोस्ट\s*ऑफिस|डाकघर)\s*के\s*(?:पास|सामने|निकट|पीछे|बाजू\s*में)|(?:near|opp(?:osite)?|behind|beside)\s+[A-Za-z0-9\u0900-\u0D7F]|(?:मेन\s*रो[डड़]|जी\.?टी\.?\s*रो[डड़]|स्टेशन\s*रो[डड़]|बाजार|बाज़ार|मण्डी|मंडी|तहसील|जिला|जि\.)|No:?|D\.?No:?|Door\s*No:?|Shop\s*No:?)/i;

/**
 * Classifies a signboard into one of three distinct operational archetypes.
 */
export function classifySignboardArchetype(blocks: BoundingBox[]): SignboardArchetype {
  const combined = blocks.map((b) => b.text || '').join(' ').toLowerCase();

  // 1. Check for Commercial Store, Clinic, Restaurant, or Retail FIRST
  // Stores often have street addresses in their footer (e.g. "No: 2/60, Nalla Thambi Street"),
  // but they have merchandise, shop words, healthcare facilities, or contact phone numbers.
  const hasPhone = /(?:\+91[\-\s]?)?[6-9]\d{4}[\s\-\.]?\d{5}\b|(?:\+91[\-\s]?)?[6-9]\d{9}\b/i.test(combined);
  const commercialRegex =
    /\b(store|shop|mart|clinic|hospital|dispensary|nursing\s*home|maternity|healthcare|diagnostic|pathology|scan|x-ray|blood\s*bank|casualty|emergency|trauma|icu|opd|ayush|ayurveda|homeopathy|dental|dentist|eye\s*care|opticals|ent|pediatric|ortho|orthopedic|gynec|cardio|pharma|pharmacy|chemist|medical|druggist|dr\.?|doctor|book|books|school|guides|ncert|hotel|restaurant|bakery|jewel|jewellers|textiles|silks|tailor|sales|service|sweets|hardware|traders|cafe|mess|bhavan|dharbar|agency|enterprises|d\.?no|door\s*no|shop\s*no|plot\s*no|अस्पताल|दवाखाना|चिकित्सालय|औषधालय|क्लिनिक|दुकान|स्टोर|மருத்துவமனை|மருந்தகம்|அங்காடி|ఆసుపత్రి|దుకాణం|స్టోర్|ಆಸ್ಪತ್ರೆ|ಅಂಗಡಿ)\b/i;

  if (commercialRegex.test(combined) || hasPhone) {
    return 'COMMERCIAL_STORE';
  }

  // 2. Transit & Station or Regulatory sign
  const transitRegex =
    /\b(platform|pf[\s-]?\d|railway|train|coach|fob|foot\s*overbridge|overbridge|waiting\s*hall|waiting\s*room|cloak\s*room|ticket\s*counter|ticket|exit\s*gate|entry\s*gate|no\s*parking|stop|dead\s*end|one\s*way|school\s*ahead|प्लेटफॉर्म|रेलवे|गाड़ी|ପ୍ଲାଟଫର୍ମ|ரயில்|ప్లాట్‌ఫాం|ರೈಲ್ವೆ)\b/i;
  if (transitRegex.test(combined)) {
    return 'TRANSIT_REGULATORY';
  }

  // 3. Street, Road, or Highway Wayfinding sign
  const wayfindingRegex =
    /\b(nh[\s-]?\d+|sh[\s-]?\d+|national\s*highway|state\s*highway|expressway|toll|toll\s*plaza|bypass|road|rd\.?|street|st\.?|marg|salai|cross|lane|circle|chowk|junction|roundabout|speed\s*limit|\b\d+\s*(?:km|kms|m|किमी|कि\.मी\.|मीटर|கி\.மீ)\b|way\s*to|towards|straight|turn\s*left|turn\s*right|keep\s*left|keep\s*right|मार्ग|రహదారి|ರಸ್ತೆ)\b/i;
  if (wayfindingRegex.test(combined)) {
    return 'STREET_WAYFINDING';
  }

  // Default: Commercial Store, Clinic, Restaurant, Pharmacy, Showroom
  return 'COMMERCIAL_STORE';
}

/**
 * Strips statutory codes, license noise, URLs, designer templates, and placeholder numbers.
 */
export function sanitizeGibberishAndCodes(text: string): string {
  if (!text) return '';
  return text
    .replace(URL_REGEX, ' ')
    .replace(DESIGNER_TEMPLATE_REGEX, ' ')
    .replace(DUMMY_PHONE_REGEX, ' ')
    .replace(STATUTORY_REGEX, '')
    .replace(GSTIN_REGEX, '')
    .replace(DRUG_LIC_REGEX, '')
    .replace(SERIAL_GIBBERISH_REGEX, '')
    .replace(/[()[\]{}<>;~|•*#^\\/=_~]+/g, ' ')
    .replace(/\s*[-–—:/.,]\s*$/g, '')
    .replace(/^\s*[-–—:/.,]\s*/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Splits a composite text block into services/offerings and address/landmark components.
 */
export function splitOfferingsAndAddress(text: string): { offerings: string; address: string } {
  // Split on danda '।', newline, or double space
  let parts = text.split(/(?<=[।\n])\s*|\s{2,}/);

  // If only 1 part, check if there is an explicit address boundary keyword
  if (parts.length === 1) {
    const match = text.match(
      /(.*?)(?:\s*(?:[.,;]\s*|\s+))(\b(?:near|opp(?:osite)?|behind|beside|No:?|D\.?No:?|Door\s*No:?)\b.*)/i
    );
    if (match) {
      parts = [match[1], match[2]];
    }
  }

  let address = '';
  const offerings: string[] = [];

  for (const part of parts) {
    const p = part.replace(/^[.\s,:;/-]+|[.\s,:;/-]+$/g, '').trim();
    if (!p) continue;
    if (ADDRESS_ANCHOR_REGEX.test(p)) {
      address = address ? `${address}, ${p}` : p;
    } else {
      offerings.push(p);
    }
  }

  return { offerings: offerings.join('. '), address };
}

/**
 * 1. STREET & WAYFINDING SIGNBOARD ENGINE
 * Tailored for Roads, Highways, Street Name Plaques, Distances, and Exit Ramps.
 */
function cleanWayfindingSignboard(
  blocks: BoundingBox[],
  imageWidth: number,
  imageHeight: number
): CleanedSignboardResult {
  let primaryRoute = '';
  const destinations: DestinationDistancePair[] = [];
  const semanticBlocks: SemanticBlock[] = [];
  let detectedDirection = '';
  let speedLimit = '';

  blocks.forEach((block, idx) => {
    let raw = (block.text || '').trim();
    if (!raw) return;

    // Check for Directional Arrows
    if (ARROW_REGEX.test(raw)) {
      if (/[→⇒▶]|->|-->/.test(raw)) detectedDirection = 'On your right';
      else if (/[←⇐◀]|<-|<--/.test(raw)) detectedDirection = 'On your left';
      else if (/[↑▲]|\^/.test(raw)) detectedDirection = 'Straight ahead';
      raw = raw.replace(ARROW_REGEX, '').trim();
    }

    // Check for Speed Limit (e.g., Speed Limit 80 km/h)
    const speedMatch = raw.match(/\b(?:speed\s*limit\s*)?(\d{2,3}\s*(?:km\/h|kmph))\b/i);
    if (speedMatch) {
      speedLimit = speedMatch[0].toUpperCase();
    }

    // Route / Highway Identification (e.g. NH-48, M.G. Road, Park Street, கோபாலகிருஷ்ணன் தெரு)
    const routeMatch = raw.match(
      /\b(NH[\s-]?\d+|SH[\s-]?\d+|[A-Za-z\u0900-\u0D7F\s]+(?:Road|Street|Marg|Salai|Expressway|Highway|Lane|Avenue|தெரு|சாலை|வீதி|రహదారి|ರಸ್ತೆ|गली|मार्ग|सड़क))\b/i
    );

    // Distance Extraction (e.g., 120 km, 500m)
    const distanceMatch = raw.match(/\b\d+\s*(?:km|kms|m|किमी|कि\.मी\.|मीटर)\b/i);

    if (routeMatch && !primaryRoute) {
      primaryRoute = raw;
      semanticBlocks.push({
        id: `block-${idx}-route`,
        originalText: raw,
        semanticTag: 'WAYFINDING_ROUTE',
        badgeColor: '#0284c7', // Sky Blue
        badgeLabel: 'Highway / Route Name',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });
    } else if (primaryRoute && routeMatch) {
      // If primaryRoute is already identified and this block contains a bilingual duplicate of the street name:
      // Strip the duplicated street name portion to keep only the civic / ward details
      const strippedDetail = raw
        .replace(routeMatch[0], '')
        .replace(/^[.\s,:;/-]+/, '')
        .trim();

      if (strippedDetail) {
        destinations.push({ name: strippedDetail });
        semanticBlocks.push({
          id: `block-${idx}-dest`,
          originalText: strippedDetail,
          semanticTag: 'WAYFINDING_DESTINATION',
          badgeColor: '#059669',
          badgeLabel: 'Civic / Ward Info',
          bbox_xyxy: block.bbox_xyxy,
          isClickable: true
        });
      }
    } else if (distanceMatch || /toll|plaza|airport|bypass|chowk|circle/i.test(raw)) {
      destinations.push({
        name: raw,
        distance: distanceMatch ? distanceMatch[0] : undefined
      });
      semanticBlocks.push({
        id: `block-${idx}-dest`,
        originalText: raw,
        semanticTag: 'WAYFINDING_DESTINATION',
        badgeColor: '#059669', // Emerald
        badgeLabel: 'Destination & Distance',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });
    } else {
      if (!primaryRoute) {
        primaryRoute = raw;
        semanticBlocks.push({
          id: `block-${idx}-route`,
          originalText: raw,
          semanticTag: 'WAYFINDING_ROUTE',
          badgeColor: '#0284c7',
          badgeLabel: 'Highway / Route Name',
          bbox_xyxy: block.bbox_xyxy,
          isClickable: true
        });
      } else {
        destinations.push({ name: raw });
        semanticBlocks.push({
          id: `block-${idx}-dest`,
          originalText: raw,
          semanticTag: 'WAYFINDING_DESTINATION',
          badgeColor: '#059669',
          badgeLabel: 'Wayfinding Marker',
          bbox_xyxy: block.bbox_xyxy,
          isClickable: true
        });
      }
    }
  });

  const wayfindingDetails: WayfindingDetails = {
    streetOrRouteName: primaryRoute || 'Highway / Route',
    destinations,
    maneuverGuide: detectedDirection,
    speedLimit
  };

  const destSummary = destinations.map((d) => d.name).join('. ');
  const speechPrefix = detectedDirection ? `${detectedDirection}, ` : '';

  return {
    archetype: 'STREET_WAYFINDING',
    brandName: primaryRoute || 'Highway / Street Sign',
    servicesText: destSummary,
    phoneNumbers: [],
    directionGuide: detectedDirection,
    fullSpeechScript: `${speechPrefix}${primaryRoute}. ${destSummary}`.trim(),
    semanticBlocks,
    wayfindingDetails
  };
}

/**
 * 2. TRANSIT & REGULATORY SIGNBOARD ENGINE
 * Tailored for Railway Stations, Platforms, Foot Overbridges, Gates, and Traffic Alerts.
 */
function cleanTransitRegulatorySignboard(
  blocks: BoundingBox[],
  imageWidth: number,
  imageHeight: number
): CleanedSignboardResult {
  let platformOrGate = '';
  const amenities: string[] = [];
  const semanticBlocks: SemanticBlock[] = [];
  let detectedDirection = '';
  let alertRule = '';

  blocks.forEach((block, idx) => {
    let raw = (block.text || '').trim();
    if (!raw) return;

    // Check for Directional Arrows
    if (ARROW_REGEX.test(raw)) {
      if (/[→⇒▶]|->|-->/.test(raw)) detectedDirection = 'On your right';
      else if (/[←⇐◀]|<-|<--/.test(raw)) detectedDirection = 'On your left';
      else if (/[↑▲]|\^/.test(raw)) detectedDirection = 'Straight ahead';
      raw = raw.replace(ARROW_REGEX, '').trim();
    }

    // Check for Regulatory / Caution (No Parking, Stop, Speed Limit)
    if (/\b(no\s*parking|stop|dead\s*end|one\s*way|school\s*ahead|speed\s*limit)\b/i.test(raw)) {
      alertRule = raw;
      semanticBlocks.push({
        id: `block-${idx}-alert`,
        originalText: raw,
        semanticTag: 'REGULATORY_ALERT',
        badgeColor: '#dc2626', // Red
        badgeLabel: 'Regulatory Alert',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });
      return;
    }

    // Platform or Gate Identification
    const pfMatch = raw.match(/\b(platform[\s-]?\d+|pf[\s-]?\d+|gate[\s-]?\d+|प्लेटफॉर्म[\s-]?\d+)\b/i);
    if (pfMatch || (!platformOrGate && /platform|प्लेटफॉर्म|gate/i.test(raw))) {
      platformOrGate = raw;
      semanticBlocks.push({
        id: `block-${idx}-platform`,
        originalText: raw,
        semanticTag: 'TRANSIT_PLATFORM',
        badgeColor: '#7c3aed', // Purple
        badgeLabel: 'Platform / Gate',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });
    } else {
      amenities.push(raw);
      semanticBlocks.push({
        id: `block-${idx}-amenity`,
        originalText: raw,
        semanticTag: 'SERVICES',
        badgeColor: '#059669', // Green
        badgeLabel: 'Transit Facility',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });
    }
  });

  const transitDetails: TransitDetails = {
    transitHubOrStation: 'Railway / Transit Station',
    platformOrGate,
    amenities,
    guidanceNote: alertRule || (detectedDirection ? `Proceed ${detectedDirection}` : undefined)
  };

  const amenitiesSummary = amenities.join('. ');
  const speechPrefix = detectedDirection ? `${detectedDirection}, ` : '';

  return {
    archetype: 'TRANSIT_REGULATORY',
    brandName: platformOrGate || alertRule || 'Transit Sign',
    servicesText: amenitiesSummary,
    phoneNumbers: [],
    directionGuide: detectedDirection,
    fullSpeechScript: `${speechPrefix}${platformOrGate || alertRule}. ${amenitiesSummary}`.trim(),
    semanticBlocks,
    transitDetails
  };
}

/**
 * 3. COMMERCIAL STORE & CLINIC SIGNBOARD ENGINE
 * Tailored for Retail Stores, Clinics, Restaurants, Showrooms, Pharmacies, Hours, and Phone dialers.
 */
function cleanCommercialSignboard(
  blocks: BoundingBox[],
  imageWidth: number,
  imageHeight: number
): CleanedSignboardResult {
  const phoneNumbers: string[] = [];
  const validServiceLines: string[] = [];
  const semanticBlocks: SemanticBlock[] = [];
  let brandCandidate = '';
  let highestProminence = -1;
  let detectedDirection = '';
  let detectedHours = '';
  let extractedAddress = '';
  let mandatoryNotice = '';

  blocks.forEach((block, idx) => {
    let raw = (block.text || '').trim();
    if (!raw) return;

    const [x1, y1, x2, y2] = block.bbox_xyxy;
    const width = Math.max(1, x2 - x1);
    const height = Math.max(1, y2 - y1);
    const area = width * height;
    const yCenter = (y1 + y2) / 2;

    // 1. Purge Dummy / Placeholder Phone Numbers immediately (e.g. 1234567890)
    raw = raw.replace(DUMMY_PHONE_REGEX, ' ').trim();

    // 2. Check for Directional Arrows
    if (ARROW_REGEX.test(raw)) {
      if (/[→⇒▶]|->|-->/.test(raw)) detectedDirection = 'On your right';
      else if (/[←⇐◀]|<-|<--/.test(raw)) detectedDirection = 'On your left';
      else if (/[↑▲]|\^/.test(raw)) detectedDirection = 'Straight ahead';
      raw = raw.replace(ARROW_REGEX, '').trim();
    }

    // 3. Check for Real Phone Numbers
    const matchedPhones = raw.match(PHONE_REGEX);
    if (matchedPhones) {
      phoneNumbers.push(...matchedPhones);
      semanticBlocks.push({
        id: `block-${idx}-phone`,
        originalText: matchedPhones.join(', '),
        semanticTag: 'CONTACT',
        badgeColor: '#ea580c', // Orange
        badgeLabel: 'Contact Phone',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true,
        actionUrl: `tel:${matchedPhones[0]}`
      });
      raw = raw.replace(PHONE_REGEX, '').trim();
    }

    // 4. Check for Operating Hours
    if (/\b(?:time|timing|hours?|समय|ಸಮಯ|நேரம்|సమయం|সময়|વેળ|24x7|24\/7)\b/i.test(raw)) {
      detectedHours = raw;
      semanticBlocks.push({
        id: `block-${idx}-hours`,
        originalText: raw,
        semanticTag: 'HOURS',
        badgeColor: '#0284c7', // Sky Blue
        badgeLabel: 'Operating Hours',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });
      return;
    }

    // 5. Check for Critical / Mandatory Notices (e.g. 24x7 Emergency, Appointments Only, Closed Days)
    const noticeMatch = raw.match(
      /\b(?:24\s*(?:hours?|hrs)|24x7|emergency(?:\s*(?:services?|care|casualty))?|casualty|icu|trauma\s*care|appointment\s*only|closed\s*on\s*[A-Za-z]+|sunday\s*closed|emergency\s*entrance)\b/i
    );
    if (noticeMatch && !mandatoryNotice) {
      mandatoryNotice = noticeMatch[0].toUpperCase();
      semanticBlocks.push({
        id: `block-${idx}-notice`,
        originalText: noticeMatch[0],
        semanticTag: 'REGULATORY_ALERT',
        badgeColor: '#dc2626', // Red
        badgeLabel: '🚨 Emergency / Critical Notice',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });
    }

    // 6. Check for Entirely Statutory Noise, URLs, or Designer Template Watermarks (Purge from speech)
    if (
      GSTIN_REGEX.test(raw) ||
      STATUTORY_REGEX.test(raw) ||
      DRUG_LIC_REGEX.test(raw) ||
      DESIGNER_TEMPLATE_REGEX.test(raw) ||
      URL_REGEX.test(raw)
    ) {
      const sanitized = sanitizeGibberishAndCodes(raw);
      if (sanitized.length === 0 || (sanitized.length <= 2 && !/[A-Za-z0-9\u0900-\u0D7F]/.test(sanitized))) {
        semanticBlocks.push({
          id: `block-${idx}-noise`,
          originalText: raw,
          semanticTag: 'DISCARDED_NOISE',
          badgeColor: '#71717a', // Muted Gray
          badgeLabel: 'Template Watermark / Statutory Code',
          bbox_xyxy: block.bbox_xyxy,
          isClickable: false
        });
        return;
      }
    }

    // 7. Sanitize any inline gibberish, license codes, designer watermarks, or URLs
    raw = sanitizeGibberishAndCodes(raw);
    if (!raw) return;

    // 8. Bilingual Address & Offerings Separation (Isolate addresses so they never pollute services)
    const { offerings: separatedOfferings, address: foundAddress } = splitOfferingsAndAddress(raw);
    if (foundAddress) {
      if (!extractedAddress) {
        extractedAddress = foundAddress;
      } else {
        extractedAddress = `${extractedAddress}, ${foundAddress}`;
      }
      semanticBlocks.push({
        id: `block-${idx}-addr`,
        originalText: foundAddress,
        semanticTag: 'WAYFINDING_ROUTE',
        badgeColor: '#0284c7', // Sky Blue
        badgeLabel: 'Store / Clinic Address',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });
      raw = separatedOfferings.trim();
    }
    if (!raw) return;

    // 6. Prominence Formula: Area * vertical weight * aspect ratio
    const verticalWeight = 1.0 - 0.5 * (yCenter / imageHeight);
    const aspectBonus = width / height > 2.0 ? 1.2 : 1.0;
    const prominence = area * verticalWeight * aspectBonus;

    const isTitleTagged = block.type === 'Title' || block.type === 'Section-title';
    const effectiveProminence = isTitleTagged ? prominence * 2.0 : prominence;

    // Check if block contains both Store/Hospital Name and Offerings (e.g. "VIIAY BOOK STORE State Board, Central, NCERT...")
    const storeSplitMatch = raw.match(
      /^([A-Za-z\u0900-\u0D7F\s&.'-]+?\b(?:Book\s*Store|Store|Shop|Mart|Clinic|Hospital|Dispensary|Nursing\s*Home|Maternity|Healthcare|Diagnostic|Pathology|Lab|Laboratories|Pharmacy|Medical|Druggist|Bakery|Jewellers|Textiles|Silks|Opticals|Sweets|Restaurant|Hotel|Cafe|Traders|Enterprises|Agency|अस्पताल|दवाखाना|चिकित्सालय|औषधालय|क्लिनिक|दुकान|स्टोर|மருத்துவமனை|மருந்தகம்))\b\s*([,.:;\-]?\s*.+)?$/i
    );
    if (storeSplitMatch && storeSplitMatch[2] && !brandCandidate) {
      const extractedBrand = storeSplitMatch[1].trim();
      const extractedOfferings = storeSplitMatch[2].replace(/^[.\s,:;/-]+/, '').trim();

      brandCandidate = extractedBrand;
      highestProminence = 999999;

      semanticBlocks.push({
        id: `block-${idx}-brand`,
        originalText: extractedBrand,
        semanticTag: 'BRAND_NAME',
        badgeColor: '#2563eb', // Blue
        badgeLabel: 'Store / Location Name',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });

      if (extractedOfferings) {
        validServiceLines.push(extractedOfferings);
        semanticBlocks.push({
          id: `block-${idx}-service`,
          originalText: extractedOfferings,
          semanticTag: 'SERVICES',
          badgeColor: '#16a34a', // Green
          badgeLabel: 'Services / Offerings',
          bbox_xyxy: block.bbox_xyxy,
          isClickable: true
        });
      }
      return;
    }

    if (effectiveProminence > highestProminence && yCenter < imageHeight * 0.65) {
      if (brandCandidate) validServiceLines.push(brandCandidate);
      highestProminence = effectiveProminence;
      brandCandidate = raw;
      semanticBlocks.push({
        id: `block-${idx}-brand`,
        originalText: raw,
        semanticTag: 'BRAND_NAME',
        badgeColor: '#2563eb', // Blue
        badgeLabel: 'Store / Location Name',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });
    } else {
      validServiceLines.push(raw);
      semanticBlocks.push({
        id: `block-${idx}-service`,
        originalText: raw,
        semanticTag: 'SERVICES',
        badgeColor: '#16a34a', // Green
        badgeLabel: 'Services / Offerings',
        bbox_xyxy: block.bbox_xyxy,
        isClickable: true
      });
    }
  });

  const uniquePhones = Array.from(new Set(phoneNumbers));
  const servicesText = validServiceLines.join('. ');
  const speechPrefix = detectedDirection ? `${detectedDirection}, ` : '';

  const commercialDetails: CommercialDetails = {
    storeName: brandCandidate,
    offerings: validServiceLines,
    timings: detectedHours,
    phoneNumbers: uniquePhones,
    address: extractedAddress,
    mandatoryNotice
  };

  const timingsNoticeStr = [detectedHours ? `Timings: ${detectedHours}` : '', mandatoryNotice ? `Notice: ${mandatoryNotice}` : '']
    .filter(Boolean)
    .join('. ');

  return {
    archetype: 'COMMERCIAL_STORE',
    brandName: brandCandidate || 'Store / Facility',
    servicesText,
    phoneNumbers: uniquePhones,
    directionGuide: detectedDirection,
    address: extractedAddress,
    mandatoryNotice,
    fullSpeechScript: `${speechPrefix}${brandCandidate}. ${servicesText}${timingsNoticeStr ? `. ${timingsNoticeStr}` : ''}`.trim(),
    semanticBlocks,
    commercialDetails
  };
}

/**
 * MASTER DUAL-ENGINE SIGNBOARD CLEANER
 * Automatically classifies incoming OCR into Street Wayfinding, Transit Regulatory,
 * or Commercial Shop, routing to the specialized parser.
 */
export function cleanSignboardBlocks(
  blocks: BoundingBox[],
  imageWidth: number = 1600,
  imageHeight: number = 1200
): CleanedSignboardResult {
  const archetype = classifySignboardArchetype(blocks);

  if (archetype === 'STREET_WAYFINDING') {
    return cleanWayfindingSignboard(blocks, imageWidth, imageHeight);
  }

  if (archetype === 'TRANSIT_REGULATORY') {
    return cleanTransitRegulatorySignboard(blocks, imageWidth, imageHeight);
  }

  return cleanCommercialSignboard(blocks, imageWidth, imageHeight);
}
