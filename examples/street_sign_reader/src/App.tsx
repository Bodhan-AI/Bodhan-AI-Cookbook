// File: src/App.tsx
import React, { useState, useEffect } from 'react';
import { Header } from './components/Header';
import { ScanScreen } from './screens/ScanScreen';
import { ResultScreen } from './screens/ResultScreen';
import { HistoryScreen } from './screens/HistoryScreen';
import { SettingsScreen } from './screens/SettingsScreen';
import {
  AppSettings,
  ProcessedScanResult,
  HistoryScanItem
} from './models/SignboardTypes';
import {
  getStoredSettings,
  saveSettings,
  applyTheme,
  saveScanToHistory
} from './services/secureStorage';
import { CompressedImageResult } from './services/imageCompressor';
import { cleanSignboardBlocks, sanitizeGibberishAndCodes } from './services/onDeviceSignCleaner';
import {
  performBodhanOcr,
  performBodhanTransliterate,
  performBodhanTranslate,
  performBodhanSpeak
} from './services/bodhanClient';
import { summarizeWithGemma, isLanguageSupportedByGemma } from './services/gemmaSummarizer';
import { ExtraUserContext } from './models/SignboardTypes';
import { SampleSign } from './assets/sampleSigns';
import { Camera, FileText, History, SlidersHorizontal, Loader2, CheckCircle2 } from 'lucide-react';

type PipelineStage = 'ocr' | 'cleaning' | 'translating' | 'gemma' | 'tts' | 'done';

export const App: React.FC = () => {
  const [settings, setSettings] = useState<AppSettings>(getStoredSettings());
  const [activeTab, setActiveTab] = useState<'scanner' | 'result' | 'history' | 'settings'>('scanner');
  const [currentResult, setCurrentResult] = useState<ProcessedScanResult | null>(null);
  const [activeSample, setActiveSample] = useState<SampleSign | undefined>(undefined);
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const [currentStage, setCurrentStage] = useState<PipelineStage>('ocr');

  const isGemmaActive =
    (settings.enableGemmaSummarizer ?? settings.enableGemmaHindi ?? true) &&
    isLanguageSupportedByGemma(settings.targetLanguage);

  useEffect(() => {
    applyTheme(settings.theme);
  }, [settings.theme]);

  const handleToggleTheme = () => {
    const isDark =
      settings.theme === 'dark' ||
      (settings.theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    const nextTheme = isDark ? 'light' : 'dark';
    const updated = saveSettings({ theme: nextTheme });
    setSettings(updated);
  };

  const handleSaveSettings = (updated: Partial<AppSettings>) => {
    const next = saveSettings(updated);
    setSettings(next);
  };

  // The Bodhan-Native Lean Pipeline Execution
  const handleProcessImage = async (compressed: CompressedImageResult, sample?: SampleSign) => {
    setIsProcessing(true);
    setCurrentStage('ocr');
    const startTime = performance.now();
    const effectiveSample = sample !== undefined ? sample : activeSample;
    if (sample !== undefined) {
      setActiveSample(sample);
    }

    try {
      // 1. Bodhan Indic-OCR
      setCurrentStage('ocr');
      const rawBlocks = effectiveSample ? effectiveSample.rawBlocks : await performBodhanOcr(compressed.dataUrl);

      // 2. On-Device Signboard Cleaner (Local CPU, ~3ms)
      setCurrentStage('cleaning');
      const cleaned = cleanSignboardBlocks(rawBlocks, compressed.width, compressed.height);

      // Safety Guard: Check if OCR detected any readable text
      const hasRecognizedText =
        rawBlocks &&
        rawBlocks.length > 0 &&
        (Boolean(cleaned.brandName?.trim()) ||
          Boolean(cleaned.servicesText?.trim()) ||
          cleaned.phoneNumbers.length > 0);

      if (!hasRecognizedText) {
        // No text detected: Halt pipeline immediately to prevent hallucinations & wasted TTS calls
        const noTextMessages: Record<string, string> = {
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
        const noTextMessage = noTextMessages[settings.targetLanguage] || noTextMessages.en;

        let audioWavUrl: string | undefined;
        let audioDurationSec: number | undefined;
        try {
          const speakRes = await performBodhanSpeak(
            noTextMessage,
            settings.preferredVoice,
            settings.targetLanguage
          );
          audioWavUrl = speakRes.audioUrl;
          audioDurationSec = speakRes.durationSec;
        } catch (e) {
          console.warn('Speech synthesis for guidance skipped:', e);
        }

        const emptyResult: ProcessedScanResult = {
          id: `scan-${Date.now()}`,
          timestamp: Date.now(),
          imageUrl: compressed.dataUrl,
          imageDimensions: { width: compressed.width, height: compressed.height },
          targetLanguage: settings.targetLanguage,
          targetVoice: settings.preferredVoice,
          cleaned,
          transliteratedBrand: '',
          translatedServices: '',
          masterSpeechScript: noTextMessage,
          rawConcatenatedScript: '',
          isGemmaSynthesized: false,
          audioWavUrl,
          audioDurationSec,
          extraContext: {
            domain: 'COMMERCIAL',
            domainLabel: '⚠️ No Text Detected',
            domainIcon: 'alert',
            keyDetails: ['0 text blocks detected', 'Photo blurry or occluded'],
            guidanceTip: noTextMessage
          },
          processingTimeMs: Math.round(performance.now() - startTime)
        };

        setCurrentResult(emptyResult);
        setActiveTab('result');
        return;
      }

      // 3. Parallel Bodhan Transliteration (Proper Nouns) & Translation (Services)
      setCurrentStage('translating');
      let [transliteratedBrand, translatedServices] = await Promise.all([
        performBodhanTransliterate(cleaned.brandName, settings.targetLanguage),
        performBodhanTranslate(cleaned.servicesText, settings.targetLanguage)
      ]);

      // Join Semantics — translate the direction guide into the target language
      let directionPhrase = cleaned.directionGuide || '';
      if (directionPhrase && settings.targetLanguage !== 'en') {
        const directionTranslations: Record<string, Record<string, string>> = {
          'On your right': {
            hi: 'आपकी दाईं ओर', ta: 'உங்கள் வலதுபுறம்', te: 'మీ కుడివైపు',
            bn: 'আপনার ডানদিকে', mr: 'तुमच्या उजवीकडे', gu: 'તમારી જમણી બાજુ',
            kn: 'ನಿಮ್ಮ ಬಲಭಾಗದಲ್ಲಿ', ml: 'നിങ്ങളുടെ വലതുവശത്ത്', pa: 'ਤੁਹਾਡੇ ਸੱਜੇ ਪਾਸੇ',
            or: 'ଆପଣଙ୍କ ଡାହାଣ ପାଖରେ', ur: 'آپ کے دائیں طرف'
          },
          'On your left': {
            hi: 'आपकी बाईं ओर', ta: 'உங்கள் இடதுபுறம்', te: 'మీ ఎడమవైపు',
            bn: 'আপনার বাঁদিকে', mr: 'तुमच्या डावीकडे', gu: 'તમારી ડાબી બાજુ',
            kn: 'ನಿಮ್ಮ ಎಡಭಾಗದಲ್ಲಿ', ml: 'നിങ്ങളുടെ ഇടതുവശത്ത്', pa: 'ਤੁਹਾਡੇ ਖੱਬੇ ਪਾਸੇ',
            or: 'ଆପଣଙ୍କ ବାମ ପାଖରେ', ur: 'آپ کے بائیں طرف'
          },
          'Straight ahead': {
            hi: 'सीधे आगे', ta: 'நேராக முன்னே', te: 'నేరుగా ముందుకు',
            bn: 'সোজা সামনে', mr: 'सरळ पुढे', gu: 'સીધા આગળ',
            kn: 'ನೇರವಾಗಿ ಮುಂದೆ', ml: 'നേരെ മുന്നിൽ', pa: 'ਸਿੱਧੇ ਅੱਗੇ',
            or: 'ସିଧା ଆଗକୁ', ur: 'سیدھا آگے'
          }
        };
        const lang = settings.targetLanguage;
        directionPhrase = directionTranslations[directionPhrase]?.[lang] || directionPhrase;
      }
      const directionPrefix = directionPhrase ? `${directionPhrase}, ` : '';
      const rawConcatenation = `${directionPrefix}${transliteratedBrand || cleaned.brandName}. ${
        translatedServices || cleaned.servicesText
      }`.trim();

      let masterSpeechScript = rawConcatenation;
      let isGemmaSynthesized = false;
      let gemmaModelUsed: string | undefined;
      let gemmaProvider: string | undefined;
      let extraContext: ExtraUserContext | undefined;

      const isGemmaActive =
    (settings.enableGemmaSummarizer ?? settings.enableGemmaHindi ?? true) &&
    isLanguageSupportedByGemma(settings.targetLanguage);

      // Gemma Conversational Summarizer Layer (Active for ALL 11 Indian languages)
      if (isGemmaActive) {
        setCurrentStage('gemma');
        try {
          const hoursBlock = cleaned.semanticBlocks.find((b) => b.semanticTag === 'HOURS');

          // Pass only sanitized, non-noise text blocks to Gemma (purging GSTIN, licenses & serial gibberish)
          const cleanTexts = cleaned.semanticBlocks
            .filter((b) => b.semanticTag !== 'DISCARDED_NOISE')
            .map((b) => sanitizeGibberishAndCodes(b.originalText))
            .filter((t) => t.length > 1);

          const gemmaResult = await summarizeWithGemma(
            {
              brandName: transliteratedBrand || cleaned.brandName,
              servicesText: translatedServices || cleaned.servicesText,
              directionGuide: directionPhrase,
              hoursText: hoursBlock?.originalText,
              targetLanguage: settings.targetLanguage,
              rawBlocks: cleanTexts.length > 0 ? cleanTexts : rawBlocks.map((b) => b.text),
              archetype: cleaned.archetype
            },
            settings
          );

          masterSpeechScript = gemmaResult.synthesizedScript;
          isGemmaSynthesized = true;
          gemmaModelUsed = gemmaResult.modelUsed;
          gemmaProvider = gemmaResult.source;
          extraContext = gemmaResult.extraContext;

          // When Gemma acts as the primary visual copilot, adopt its semantic identification
          if (gemmaResult.identification) {
            const ident = gemmaResult.identification;
            cleaned.archetype = ident.archetype;
            if (ident.name) {
              cleaned.brandName = ident.name;
              transliteratedBrand = ident.name;
            }
            if (ident.servicesText) {
              cleaned.servicesText = ident.servicesText;
              translatedServices = ident.servicesText;
            }
            if (ident.address) {
              cleaned.address = ident.address;
            }
            if (ident.timings && cleaned.commercialDetails) {
              cleaned.commercialDetails.timings = ident.timings;
            }
            if (ident.urgentNotice) {
              cleaned.mandatoryNotice = ident.urgentNotice;
            }
            if (ident.phoneNumbers && ident.phoneNumbers.length > 0) {
              cleaned.phoneNumbers = ident.phoneNumbers;
            }
          }
        } catch (gemmaErr) {
          console.warn('[Gemma Summarizer] Failed, falling back to concatenation:', gemmaErr);
        }
      } else {
        gemmaModelUsed = 'Bodhan Neural Translation (IndicTrans2)';
        gemmaProvider = 'builtin';
      }

      // 4. Bodhan Indic-Speak Text-to-Speech (24 kHz WAV)
      setCurrentStage('tts');
      let audioWavUrl: string | undefined;
      let audioDurationSec: number | undefined;

      try {
        const speakRes = await performBodhanSpeak(
          masterSpeechScript,
          settings.preferredVoice,
          settings.targetLanguage
        );
        audioWavUrl = speakRes.audioUrl;
        audioDurationSec = speakRes.durationSec;
      } catch (ttsErr) {
        console.warn('Speech synthesis skipped:', ttsErr);
      }

      setCurrentStage('done');

      const totalTimeMs = Math.round(performance.now() - startTime);

      // Map translations back to each semantic block individually for accurate Tap-to-Speak
      const enrichedSemanticBlocks = await Promise.all(
        cleaned.semanticBlocks.map(async (block) => {
          if (!block.originalText) return block;

          // If block text matches primary brand, use the transliterated brand
          if (block.originalText === cleaned.brandName && transliteratedBrand) {
            return { ...block, translatedText: transliteratedBrand };
          }

          // If target language is non-English, translate each block's individual text into target language
          if (settings.targetLanguage !== 'en') {
            try {
              const translated = await performBodhanTranslate(block.originalText, settings.targetLanguage);
              return { ...block, translatedText: translated || block.originalText };
            } catch {
              return { ...block, translatedText: block.originalText };
            }
          }

          // If target language is English and block is in an Indic script (e.g. Tamil/Hindi), translate to English
          if (settings.targetLanguage === 'en' && /[\u0900-\u0D7F]/.test(block.originalText)) {
            try {
              const translated = await performBodhanTranslate(block.originalText, 'en');
              return { ...block, translatedText: translated || block.originalText };
            } catch {
              return { ...block, translatedText: block.originalText };
            }
          }

          return { ...block, translatedText: block.originalText };
        })
      );

      // Use the translated direction in the stored result
      const enrichedCleaned = {
        ...cleaned,
        semanticBlocks: enrichedSemanticBlocks,
        directionGuide: directionPhrase || cleaned.directionGuide
      };

      const processed: ProcessedScanResult = {
        id: `scan-${Date.now()}`,
        timestamp: Date.now(),
        imageUrl: compressed.dataUrl,
        imageDimensions: { width: compressed.width, height: compressed.height },
        targetLanguage: settings.targetLanguage,
        targetVoice: settings.preferredVoice,
        cleaned: enrichedCleaned,
        transliteratedBrand,
        translatedServices,
        masterSpeechScript,
        rawConcatenatedScript: rawConcatenation,
        isGemmaSynthesized,
        gemmaModelUsed,
        gemmaProvider,
        extraContext,
        audioWavUrl,
        audioDurationSec,
        processingTimeMs: totalTimeMs
      };

      // Save to Offline History (FR-14)
      const historyItem: HistoryScanItem = {
        id: processed.id,
        timestamp: processed.timestamp,
        thumbnailUrl: processed.imageUrl,
        brandName: transliteratedBrand || cleaned.brandName || 'Signboard',
        targetLanguage: settings.targetLanguage,
        servicesSummary: (translatedServices || cleaned.servicesText).slice(0, 100),
        phoneNumbers: cleaned.phoneNumbers,
        audioWavUrl: processed.audioWavUrl
      };
      saveScanToHistory(historyItem);

      setCurrentResult(processed);
      setActiveTab('result');
    } catch (err: any) {
      alert(`Pipeline error: ${err.message}`);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleReplayFromHistory = (_item: HistoryScanItem) => {
    // Playback state and single-audio exclusivity are managed directly in HistoryScreen
  };

  return (
    <div className="app-container">
      {/* Header matching console.bodhan.ai */}
      <Header
        settings={settings}
        onToggleTheme={handleToggleTheme}
        onOpenSettings={() => setActiveTab('settings')}
        activeTab={activeTab}
      />

      {/* Navigation Tab Bar */}
      <nav className="nav-tabbar">
        <button
          onClick={() => setActiveTab('scanner')}
          className={`nav-tab ${activeTab === 'scanner' ? 'active' : ''}`}
        >
          <Camera size={16} />
          Scanner
        </button>

        {currentResult && (
          <button
            onClick={() => setActiveTab('result')}
            className={`nav-tab ${activeTab === 'result' ? 'active' : ''}`}
          >
            <FileText size={16} />
            Result
          </button>
        )}

        <button
          onClick={() => setActiveTab('history')}
          className={`nav-tab ${activeTab === 'history' ? 'active' : ''}`}
        >
          <History size={16} />
          History
        </button>

        <button
          onClick={() => setActiveTab('settings')}
          className={`nav-tab ${activeTab === 'settings' ? 'active' : ''}`}
        >
          <SlidersHorizontal size={16} />
          Settings
        </button>
      </nav>

      {/* Main Content Area */}
      <main style={{ flex: 1, position: 'relative' }}>
        {/* Processing Loading Overlay */}
        {isProcessing && (
          <div
            style={{
              position: 'fixed',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              backgroundColor: 'rgba(0, 0, 0, 0.72)',
              backdropFilter: 'blur(8px)',
              zIndex: 100,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '20px'
            }}
          >
            <div
              className="card"
              style={{
                width: '100%',
                maxWidth: '460px',
                padding: '24px',
                display: 'flex',
                flexDirection: 'column',
                gap: '18px',
                boxShadow: 'var(--shadow-lg)',
                backgroundColor: 'var(--surface)'
              }}
            >
              {/* Header with active status */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <Loader2
                    size={24}
                    color="var(--brand)"
                    style={{ animation: 'spin 1s linear infinite' }}
                  />
                  <div>
                    <div style={{ fontSize: '1.05rem', fontWeight: 700, color: 'var(--ink)' }}>
                      {currentStage === 'ocr' && 'Scanning Signboard...'}
                      {currentStage === 'cleaning' && 'Analyzing Layout...'}
                      {currentStage === 'translating' && 'Translating Content...'}
                      {currentStage === 'gemma' && 'Synthesizing Speech (Gemma Summarizer)...'}
                      {currentStage === 'tts' && 'Synthesizing Audio...'}
                      {currentStage === 'done' && 'Finalizing Result...'}
                    </div>
                    <div style={{ fontSize: '0.74rem', color: 'var(--ink-3)' }}>
                      Bodhan Sovereign AI Pipeline
                    </div>
                  </div>
                </div>

                <span className="badge badge-brand">
                  {currentStage === 'ocr' && 'indic-ocr'}
                  {currentStage === 'cleaning' && 'Local CPU 3ms'}
                  {currentStage === 'translating' && 'indic-translate'}
                  {currentStage === 'gemma' && 'Gemma Conversational'}
                  {currentStage === 'tts' && 'indic-speak'}
                  {currentStage === 'done' && 'Complete'}
                </span>
              </div>

              {/* Progress bar */}
              <div
                style={{
                  width: '100%',
                  height: '6px',
                  backgroundColor: 'var(--sunken-2)',
                  borderRadius: 'var(--r-full)',
                  overflow: 'hidden'
                }}
              >
                <div
                  style={{
                    height: '100%',
                    backgroundColor: 'var(--brand)',
                    borderRadius: 'var(--r-full)',
                    transition: 'width 0.3s ease',
                    width:
                      currentStage === 'ocr'
                        ? '20%'
                        : currentStage === 'cleaning'
                        ? '40%'
                        : currentStage === 'translating'
                        ? '60%'
                        : currentStage === 'gemma'
                        ? '80%'
                        : currentStage === 'tts'
                        ? '92%'
                        : '100%'
                  }}
                />
              </div>

              {/* Sequential Steps List */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {[
                  {
                    id: 'ocr',
                    name: 'Scanning & OCR',
                    action: 'Detecting text layout & pixel coordinates',
                    model: 'indic-ocr'
                  },
                  {
                    id: 'cleaning',
                    name: 'On-Device Layout Cleaner',
                    action: 'Purging GSTIN/licenses, mapping arrows',
                    model: 'Local CPU (3ms)'
                  },
                  {
                    id: 'translating',
                    name: 'Translation & Transliteration',
                    action: 'Transliterating brand & translating services',
                    model: 'POST /translate'
                  },
                  ...(isGemmaActive
                    ? [
                        {
                          id: 'gemma',
                          name: 'Gemma Conversational Synthesizer',
                          action: 'Summarizing wayfinding, transit, and shop data into natural speech',
                          model:
                            settings.gemmaProvider === 'builtin'
                              ? 'Gemma Multilingual Engine'
                              : settings.gemmaModel || 'Gemma'
                        }
                      ]
                    : []),
                  {
                    id: 'tts',
                    name: 'Speech Synthesis',
                    action: 'Generating 24 kHz studio voice audio',
                    model: 'indic-speak'
                  }
                ].map((step, idx) => {
                  const stageOrder = isGemmaActive
                    ? ['ocr', 'cleaning', 'translating', 'gemma', 'tts', 'done']
                    : ['ocr', 'cleaning', 'translating', 'tts', 'done'];
                  const stageIndex = stageOrder.indexOf(currentStage);
                  const isDone = stageIndex > idx;
                  const isActive = stageIndex === idx;

                  return (
                    <div
                      key={step.id}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '10px 14px',
                        borderRadius: 'var(--r-md)',
                        backgroundColor: isActive
                          ? 'var(--brand-wash)'
                          : isDone
                          ? 'var(--ok-wash)'
                          : 'var(--sunken)',
                        border: `1px solid ${
                          isActive
                            ? 'var(--brand-line)'
                            : isDone
                            ? 'rgba(15, 122, 78, 0.25)'
                            : 'var(--rule)'
                        }`,
                        transition: 'all 0.2s ease'
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        {isDone ? (
                          <CheckCircle2 size={18} color="var(--ok)" />
                        ) : isActive ? (
                          <Loader2
                            size={18}
                            color="var(--brand)"
                            style={{ animation: 'spin 1s linear infinite' }}
                          />
                        ) : (
                          <div
                            style={{
                              width: '18px',
                              height: '18px',
                              borderRadius: '50%',
                              border: '2px solid var(--rule)',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              fontSize: '10px',
                              color: 'var(--ink-3)'
                            }}
                          >
                            {idx + 1}
                          </div>
                        )}

                        <div>
                          <div
                            style={{
                              fontSize: '0.86rem',
                              fontWeight: 600,
                              color: isActive
                                ? 'var(--brand)'
                                : isDone
                                ? 'var(--ok)'
                                : 'var(--ink)'
                            }}
                          >
                            {step.name}
                          </div>
                          <div style={{ fontSize: '0.72rem', color: 'var(--ink-3)' }}>
                            {step.action}
                          </div>
                        </div>
                      </div>

                      <span
                        className={`badge ${
                          isDone ? 'badge-ok' : isActive ? 'badge-brand' : 'badge-gray'
                        }`}
                        style={{ fontSize: '10px' }}
                      >
                        {isDone ? 'Done' : isActive ? 'Active' : 'Pending'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
            <style>{`@keyframes spin { 100% { transform: rotate(360deg); } }`}</style>
          </div>
        )}

        {activeTab === 'scanner' && (
          <ScanScreen
            settings={settings}
            onOpenSettings={() => setActiveTab('settings')}
            onProcessImage={handleProcessImage}
            onUpdateSettings={handleSaveSettings}
          />
        )}

        {activeTab === 'result' && currentResult && (
          <ResultScreen
            result={currentResult}
            settings={settings}
            onNewScan={() => {
              setActiveSample(undefined);
              setActiveTab('scanner');
            }}
            onReCrop={(compressed) => handleProcessImage(compressed, activeSample)}
          />
        )}

        {activeTab === 'history' && (
          <HistoryScreen
            onReplayScan={handleReplayFromHistory}
            onGoToScan={() => setActiveTab('scanner')}
          />
        )}

        {activeTab === 'settings' && (
          <SettingsScreen settings={settings} onSaveSettings={handleSaveSettings} />
        )}
      </main>

      {/* Footer */}
      <footer
        style={{
          padding: '16px 20px',
          borderTop: '1px solid var(--rule)',
          backgroundColor: 'var(--sunken)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          fontSize: '0.74rem',
          color: 'var(--ink-3)'
        }}
      >
        <div>BoardVaani • Street Sign &amp; Shop Board Reader</div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <span>100% Standalone</span>
          <span>•</span>
          <span>Sovereign Indic AI</span>
        </div>
      </footer>
    </div>
  );
};
