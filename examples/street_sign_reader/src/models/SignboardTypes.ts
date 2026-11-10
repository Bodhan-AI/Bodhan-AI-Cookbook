// File: src/models/SignboardTypes.ts

export interface BoundingBox {
  bbox_xyxy: [number, number, number, number]; // [x1, y1, x2, y2]
  text: string;
  type?: string;
}

export type SemanticTag =
  | 'BRAND_NAME'
  | 'SERVICES'
  | 'CONTACT'
  | 'HOURS'
  | 'DIRECTION'
  | 'WAYFINDING_ROUTE'
  | 'WAYFINDING_DESTINATION'
  | 'TRANSIT_PLATFORM'
  | 'REGULATORY_ALERT'
  | 'DISCARDED_NOISE';

export interface SemanticBlock {
  id: string;
  originalText: string;
  translatedText?: string;
  semanticTag: SemanticTag;
  badgeColor: string;
  badgeLabel: string;
  bbox_xyxy: [number, number, number, number];
  isClickable: boolean;
  actionUrl?: string;
}

export type SignboardArchetype =
  | 'STREET_WAYFINDING'
  | 'COMMERCIAL_STORE'
  | 'TRANSIT_REGULATORY';

export interface DestinationDistancePair {
  name: string;
  distance?: string;
  direction?: string;
}

export interface WayfindingDetails {
  streetOrRouteName: string;
  destinations: DestinationDistancePair[];
  maneuverGuide?: string;
  speedLimit?: string;
}

export interface CommercialDetails {
  storeName: string;
  offerings: string[];
  timings?: string;
  phoneNumbers: string[];
  address?: string;
  mandatoryNotice?: string;
}

export interface TransitDetails {
  transitHubOrStation: string;
  platformOrGate?: string;
  amenities: string[];
  guidanceNote?: string;
}

export interface CleanedSignboardResult {
  archetype: SignboardArchetype;
  brandName: string;
  servicesText: string;
  phoneNumbers: string[];
  directionGuide?: string;
  address?: string;
  mandatoryNotice?: string;
  fullSpeechScript: string;
  semanticBlocks: SemanticBlock[];
  wayfindingDetails?: WayfindingDetails;
  commercialDetails?: CommercialDetails;
  transitDetails?: TransitDetails;
}

export type SignboardDomain =
  | 'ROAD_HIGHWAY'
  | 'RAILWAY_TRANSIT'
  | 'COMMERCIAL'
  | 'CIVIC_PUBLIC';

export interface ExtraUserContext {
  domain: SignboardDomain;
  domainLabel: string;
  domainIcon: string;
  keyDetails: string[];
  guidanceTip: string;
}

export interface ProcessedScanResult {
  id: string;
  timestamp: number;
  imageUrl: string;
  imageDimensions: { width: number; height: number };
  detectedScript?: string;
  targetLanguage: string;
  targetVoice: string;
  cleaned: CleanedSignboardResult;
  transliteratedBrand: string;
  translatedServices: string;
  masterSpeechScript: string;
  isGemmaSynthesized?: boolean;
  rawConcatenatedScript?: string;
  gemmaModelUsed?: string;
  gemmaProvider?: string;
  extraContext?: ExtraUserContext;
  audioWavUrl?: string;
  audioDurationSec?: number;
  processingTimeMs: number;
}

export interface LanguageVoiceCatalogItem {
  code: string;
  name: string;
  nativeName: string;
  zone: 'Southern' | 'Western & Central' | 'Eastern' | 'Northern' | 'Global';
  defaultVoice: string;
  voices: {
    name: string;
    gender: 'Female' | 'Male';
  }[];
}

export interface HistoryScanItem {
  id: string;
  timestamp: number;
  thumbnailUrl: string;
  brandName: string;
  targetLanguage: string;
  servicesSummary: string;
  phoneNumbers: string[];
  audioWavUrl?: string;
}

export interface AppSettings {
  bodhanApiKey: string; // Master key (or fallback)
  bodhanOcrKey?: string; // Specific key for indic-ocr
  bodhanTranslateKey?: string; // Specific key for indic-translate
  bodhanTransliterateKey?: string; // Specific key for indic-transliterate
  bodhanTtsKey?: string; // Specific key for indic-speak
  useSeparateKeys: boolean;
  targetLanguage: string;
  preferredVoice: string;
  playbackSpeed: number; // 1.0 or 1.25
  autoPlayAudio: boolean;
  enableImageCropper?: boolean; // Whether to prompt crop modal before scanning (default: true)
  offlineDemoMode: boolean;
  theme: 'light' | 'dark' | 'system';
  enableGemmaSummarizer?: boolean; // Master toggle for all 11 languages
  enableGemmaHindi?: boolean; // Backwards-compatible alias
  includeExtraContext?: boolean; // Provide guidance context to user
  gemmaProvider?: 'builtin' | 'groq' | 'openrouter' | 'custom';
  gemmaApiKey?: string;
  gemmaEndpoint?: string;
  gemmaModel?: string;
}
