// File: src/screens/SettingsScreen.tsx
import React, { useState } from 'react';
import { AppSettings } from '../models/SignboardTypes';
import { BODHAN_LANGUAGES, getLanguageByCode } from '../models/voiceCatalog';
import { summarizeWithGemma, isLanguageSupportedByGemma, GEMMA_SUPPORTED_LANGUAGES } from '../services/gemmaSummarizer';
import {
  Key,
  Eye,
  EyeOff,
  Save,
  CheckCircle,
  CheckCircle2,
  ExternalLink,
  Sliders,
  Globe,
  CheckSquare,
  Square,
  Loader2,
  Sparkles,
  Bot,
  Play,
  ShieldCheck
} from 'lucide-react';

interface SettingsScreenProps {
  settings: AppSettings;
  onSaveSettings: (updated: Partial<AppSettings>) => void;
}

export const SettingsScreen: React.FC<SettingsScreenProps> = ({ settings, onSaveSettings }) => {
  const [useSeparateKeys, setUseSeparateKeys] = useState<boolean>(settings.useSeparateKeys || false);
  const [apiKey, setApiKey] = useState<string>(settings.bodhanApiKey || '');
  const [ocrKey, setOcrKey] = useState<string>(settings.bodhanOcrKey || '');
  const [translateKey, setTranslateKey] = useState<string>(settings.bodhanTranslateKey || '');
  const [transliterateKey, setTransliterateKey] = useState<string>(settings.bodhanTransliterateKey || '');
  const [ttsKey, setTtsKey] = useState<string>(settings.bodhanTtsKey || '');

  const [showMasterKey, setShowMasterKey] = useState<boolean>(false);
  const [showOcrKey, setShowOcrKey] = useState<boolean>(false);
  const [showTranslateKey, setShowTranslateKey] = useState<boolean>(false);
  const [showTransliterateKey, setShowTransliterateKey] = useState<boolean>(false);
  const [showTtsKey, setShowTtsKey] = useState<boolean>(false);

  const [targetLang, setTargetLang] = useState<string>(settings.targetLanguage);
  const [preferredVoice, setPreferredVoice] = useState<string>(settings.preferredVoice);
  const [autoPlay, setAutoPlay] = useState<boolean>(settings.autoPlayAudio);
  const [enableImageCropper, setEnableImageCropper] = useState<boolean>(settings.enableImageCropper !== false);

  // Gemma Conversational Synthesizer settings (All 11 Languages)
  const [enableGemmaSummarizer, setEnableGemmaSummarizer] = useState<boolean>(
    settings.enableGemmaSummarizer ?? settings.enableGemmaHindi ?? true
  );
  const [includeExtraContext, setIncludeExtraContext] = useState<boolean>(
    settings.includeExtraContext ?? true
  );
  const [gemmaProvider, setGemmaProvider] = useState<'builtin' | 'groq' | 'openrouter' | 'custom'>(
    settings.gemmaProvider || 'builtin'
  );
  const [gemmaApiKey, setGemmaApiKey] = useState<string>(settings.gemmaApiKey || '');
  const [gemmaModel, setGemmaModel] = useState<string>(
    settings.gemmaModel && settings.gemmaModel !== 'gemma2-9b-it'
      ? settings.gemmaModel
      : 'llama-3.3-70b-versatile'
  );
  const [showGemmaKey, setShowGemmaKey] = useState<boolean>(false);
  const [testDomainType, setTestDomainType] = useState<'highway' | 'railway' | 'clinic'>('highway');
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [isFetchingModels, setIsFetchingModels] = useState<boolean>(false);

  const handleFetchModels = async () => {
    if (!gemmaApiKey) {
      alert('Please enter your Groq API key first.');
      return;
    }
    setIsFetchingModels(true);
    try {
      const url =
        gemmaProvider === 'openrouter'
          ? 'https://openrouter.ai/api/v1/models'
          : 'https://api.groq.com/openai/v1/models';
      const res = await fetch(url, {
        headers: {
          Authorization: `Bearer ${gemmaApiKey.trim()}`
        }
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }
      const data = await res.json();
      const models = (data.data || [])
        .map((m: any) => m.id)
        .filter((id: string) => !id.includes('whisper') && !id.includes('guard'))
        .sort();
      setAvailableModels(models);
      if (models.length > 0 && !models.includes(gemmaModel)) {
        setGemmaModel(models[0]);
      }
    } catch (err: any) {
      alert(`Failed to fetch models from ${gemmaProvider}: ${err.message}`);
    } finally {
      setIsFetchingModels(false);
    }
  };
  const [testOutput, setTestOutput] = useState<{
    raw: string;
    gemma: string;
    model: string;
    domain: string;
    tip: string;
  } | null>(null);
  const [isTestingGemma, setIsTestingGemma] = useState<boolean>(false);

  const [saveSuccess, setSaveSuccess] = useState<boolean>(false);
  const [isSaving, setIsSaving] = useState<boolean>(false);

  const selectedLangObj = getLanguageByCode(targetLang);

  const handleLanguageChange = (langCode: string) => {
    setTargetLang(langCode);
    const langObj = getLanguageByCode(langCode);
    setPreferredVoice(langObj.defaultVoice);
  };

  const handleTestGemma = async () => {
    setIsTestingGemma(true);
    try {
      let brand = 'NH-48';
      let services = 'Pune 120 km, Toll Plaza 500m, Speed Limit 80';
      let direction = 'Straight ahead';

      if (testDomainType === 'railway') {
        brand = 'Platform 3 & 4';
        services = 'Foot Overbridge Ahead, Waiting Hall, Ticket Counter, Exit Gate 2';
        direction = 'On your right';
      } else if (testDomainType === 'clinic') {
        brand = 'Agarwal Clinic';
        services = 'Pediatric specialist, Blood testing lab, 24/7 Pharmacy';
        direction = 'On your left';
      }

      const res = await summarizeWithGemma(
        {
          brandName: brand,
          servicesText: services,
          directionGuide: direction,
          hoursText: testDomainType === 'clinic' ? '24x7' : '',
          targetLanguage: targetLang
        },
        {
          ...settings,
          targetLanguage: targetLang,
          enableGemmaSummarizer,
          includeExtraContext,
          gemmaProvider,
          gemmaApiKey,
          gemmaModel
        }
      );

      setTestOutput({
        raw: res.rawConcatenation,
        gemma: res.synthesizedScript,
        model: `${res.modelUsed} (${res.processingTimeMs} ms)`,
        domain: res.extraContext.domainLabel,
        tip: res.extraContext.guidanceTip
      });
    } catch (err: any) {
      alert(`Test failed: ${err.message}`);
    } finally {
      setIsTestingGemma(false);
    }
  };

  const handleSave = () => {
    setIsSaving(true);
    onSaveSettings({
      useSeparateKeys,
      bodhanApiKey: apiKey,
      bodhanOcrKey: ocrKey,
      bodhanTranslateKey: translateKey,
      bodhanTransliterateKey: transliterateKey,
      bodhanTtsKey: ttsKey,
      targetLanguage: targetLang,
      preferredVoice: preferredVoice,
      autoPlayAudio: autoPlay,
      enableImageCropper,
      enableGemmaSummarizer,
      enableGemmaHindi: enableGemmaSummarizer,
      includeExtraContext,
      gemmaProvider,
      gemmaApiKey,
      gemmaModel
    });

    // Provide visual feedback
    setTimeout(() => {
      setIsSaving(false);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2800);
    }, 200);
  };

  return (
    <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Title */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Sliders size={20} color="var(--brand)" />
          <div style={{ fontSize: '1.1rem', fontWeight: 600, color: 'var(--ink)' }}>
            Settings &amp; API Keys
          </div>
        </div>

        {saveSuccess && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              color: '#16a34a',
              backgroundColor: 'rgba(22, 163, 74, 0.12)',
              border: '1px solid #16a34a',
              padding: '6px 14px',
              borderRadius: 'var(--r-md)',
              fontSize: '0.84rem',
              fontWeight: 600,
              animation: 'fadeIn 0.2s ease-out'
            }}
          >
            <CheckCircle2 size={16} color="#16a34a" />
            Settings Saved!
          </div>
        )}
      </div>

      {/* 1. BYOK Bodhan API Key Card */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">
            <Key size={18} color="var(--brand)" />
            <span>Bodhan API Credentials (BYOK)</span>
          </div>
          <span className="badge badge-brand">
            {useSeparateKeys ? 'Per-Service Keys' : 'Unified Key'}
          </span>
        </div>
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ fontSize: '0.84rem', color: 'var(--ink-2)' }}>
            Your keys are stored locally in hardware-encrypted storage (AES-256-GCM). They are never
            transmitted to any third-party server.
          </div>

          {/* Toggle for Single vs Separate Keys */}
          <div
            onClick={() => setUseSeparateKeys(!useSeparateKeys)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              cursor: 'pointer',
              userSelect: 'none',
              padding: '10px 14px',
              backgroundColor: 'var(--sunken)',
              borderRadius: 'var(--r-md)',
              border: '1px solid var(--rule)'
            }}
          >
            {useSeparateKeys ? (
              <CheckSquare size={18} color="var(--brand)" />
            ) : (
              <Square size={18} color="var(--ink-3)" />
            )}
            <div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--ink)' }}>
                Use separate API keys for each service
              </div>
              <div style={{ fontSize: '0.74rem', color: 'var(--ink-3)' }}>
                Enable if your Bodhan account has individual keys for OCR, Translate, and TTS
              </div>
            </div>
          </div>

          {!useSeparateKeys ? (
            /* Unified Master Key Input */
            <div>
              <label
                style={{
                  display: 'block',
                  fontSize: '0.84rem',
                  fontWeight: 600,
                  marginBottom: '6px'
                }}
              >
                Master Bodhan API Key (All Services):
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  type={showMasterKey ? 'text' : 'password'}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="e.g. bodhan_live_sk_..."
                  className="input-control"
                  style={{ fontFamily: "'JetBrains Mono', monospace", paddingRight: '44px' }}
                />
                <button
                  type="button"
                  onClick={() => setShowMasterKey(!showMasterKey)}
                  className="btn btn-ghost btn-icon"
                  style={{
                    position: 'absolute',
                    right: '4px',
                    top: '50%',
                    transform: 'translateY(-50%)'
                  }}
                >
                  {showMasterKey ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
          ) : (
            /* Granular Service-Specific Inputs */
            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              {/* 1. OCR Key */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                  <label style={{ fontSize: '0.84rem', fontWeight: 600 }}>1. Document OCR API Key:</label>
                  <span className="badge badge-brand">indic-ocr</span>
                </div>
                <div style={{ position: 'relative' }}>
                  <input
                    type={showOcrKey ? 'text' : 'password'}
                    value={ocrKey}
                    onChange={(e) => setOcrKey(e.target.value)}
                    placeholder="Enter OCR service key..."
                    className="input-control"
                    style={{ fontFamily: "'JetBrains Mono', monospace", paddingRight: '44px' }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowOcrKey(!showOcrKey)}
                    className="btn btn-ghost btn-icon"
                    style={{
                      position: 'absolute',
                      right: '4px',
                      top: '50%',
                      transform: 'translateY(-50%)'
                    }}
                  >
                    {showOcrKey ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>

              {/* 2. Translation Key */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                  <label style={{ fontSize: '0.84rem', fontWeight: 600 }}>2. Translation API Key:</label>
                  <span className="badge badge-brand">indic-translate</span>
                </div>
                <div style={{ position: 'relative' }}>
                  <input
                    type={showTranslateKey ? 'text' : 'password'}
                    value={translateKey}
                    onChange={(e) => setTranslateKey(e.target.value)}
                    placeholder="Enter Translation service key..."
                    className="input-control"
                    style={{ fontFamily: "'JetBrains Mono', monospace", paddingRight: '44px' }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowTranslateKey(!showTranslateKey)}
                    className="btn btn-ghost btn-icon"
                    style={{
                      position: 'absolute',
                      right: '4px',
                      top: '50%',
                      transform: 'translateY(-50%)'
                    }}
                  >
                    {showTranslateKey ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>

              {/* 3. Transliteration Key */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                  <label style={{ fontSize: '0.84rem', fontWeight: 600 }}>
                    3. Transliteration API Key (Optional):
                  </label>
                  <span className="badge badge-brand">indic-transliterate</span>
                </div>
                <div style={{ position: 'relative' }}>
                  <input
                    type={showTransliterateKey ? 'text' : 'password'}
                    value={transliterateKey}
                    onChange={(e) => setTransliterateKey(e.target.value)}
                    placeholder="Defaults to Translation key if blank..."
                    className="input-control"
                    style={{ fontFamily: "'JetBrains Mono', monospace", paddingRight: '44px' }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowTransliterateKey(!showTransliterateKey)}
                    className="btn btn-ghost btn-icon"
                    style={{
                      position: 'absolute',
                      right: '4px',
                      top: '50%',
                      transform: 'translateY(-50%)'
                    }}
                  >
                    {showTransliterateKey ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>

              {/* 4. Text-to-Speech Key */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                  <label style={{ fontSize: '0.84rem', fontWeight: 600 }}>4. Text-to-Speech (TTS) Key:</label>
                  <span className="badge badge-brand">indic-speak</span>
                </div>
                <div style={{ position: 'relative' }}>
                  <input
                    type={showTtsKey ? 'text' : 'password'}
                    value={ttsKey}
                    onChange={(e) => setTtsKey(e.target.value)}
                    placeholder="Enter Speech/TTS service key..."
                    className="input-control"
                    style={{ fontFamily: "'JetBrains Mono', monospace", paddingRight: '44px' }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowTtsKey(!showTtsKey)}
                    className="btn btn-ghost btn-icon"
                    style={{
                      position: 'absolute',
                      right: '4px',
                      top: '50%',
                      transform: 'translateY(-50%)'
                    }}
                  >
                    {showTtsKey ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>
            </div>
          )}

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <a
              href="https://console.bodhan.ai/"
              target="_blank"
              rel="noreferrer"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                color: 'var(--brand)',
                fontSize: '0.82rem',
                textDecoration: 'none',
                fontWeight: 600
              }}
            >
              Manage API Keys at console.bodhan.ai
              <ExternalLink size={12} />
            </a>

            {(apiKey || ocrKey || translateKey || ttsKey) && (
              <button
                type="button"
                onClick={() => {
                  setApiKey('');
                  setOcrKey('');
                  setTranslateKey('');
                  setTransliterateKey('');
                  setTtsKey('');
                }}
                className="btn btn-ghost"
                style={{ fontSize: '0.78rem', color: '#dc2626' }}
              >
                Clear All Keys
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 2. Target Language & Voice Settings */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">
            <Globe size={18} color="var(--brand)" />
            <span>Language &amp; Voice Catalog</span>
          </div>
          <span className="badge badge-gray">22 Languages • 45 Voices</span>
        </div>
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* Target Language Dropdown */}
          <div>
            <label
              style={{
                display: 'block',
                fontSize: '0.84rem',
                fontWeight: 600,
                marginBottom: '6px'
              }}
            >
              Target Translation Language:
            </label>
            <select
              value={targetLang}
              onChange={(e) => handleLanguageChange(e.target.value)}
              className="select-control"
            >
              {BODHAN_LANGUAGES.map((lang) => (
                <option key={lang.code} value={lang.code}>
                  {lang.name} ({lang.nativeName}) — [{lang.zone}]
                </option>
              ))}
            </select>
          </div>

          {/* Voice Persona Dropdown */}
          <div>
            <label
              style={{
                display: 'block',
                fontSize: '0.84rem',
                fontWeight: 600,
                marginBottom: '6px'
              }}
            >
              Bodhan Studio Voice Persona:
            </label>
            <select
              value={preferredVoice}
              onChange={(e) => setPreferredVoice(e.target.value)}
              className="select-control"
            >
              {selectedLangObj.voices.map((v) => (
                <option key={v.name} value={v.name}>
                  {v.name} ({v.gender}) • 24 kHz WAV
                </option>
              ))}
            </select>
          </div>

          {/* Auto-play Audio Switch */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600 }}>Auto-Play Spoken Audio</div>
              <div style={{ fontSize: '0.74rem', color: 'var(--ink-3)' }}>
                Automatically play synthesized speech when scan completes
              </div>
            </div>
            <input
              type="checkbox"
              checked={autoPlay}
              onChange={(e) => setAutoPlay(e.target.checked)}
              style={{ width: '18px', height: '18px', accentColor: 'var(--brand)', cursor: 'pointer' }}
            />
          </div>

          {/* Interactive Signboard Cropper Switch */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: '1px solid var(--rule)', paddingTop: '14px' }}>
            <div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600 }}>Interactive Signboard Cropper</div>
              <div style={{ fontSize: '0.74rem', color: 'var(--ink-3)' }}>
                Open photo cropper before scanning to frame signboards and trim street background
              </div>
            </div>
            <input
              type="checkbox"
              checked={enableImageCropper}
              onChange={(e) => setEnableImageCropper(e.target.checked)}
              style={{ width: '18px', height: '18px', accentColor: 'var(--brand)', cursor: 'pointer' }}
            />
          </div>
        </div>
      </div>

      {/* 3. Gemma Conversational Synthesizer Card */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">
            <Bot size={18} color="#6366f1" />
            <span>Gemma Conversational Synthesizer</span>
          </div>
          {enableGemmaSummarizer && isLanguageSupportedByGemma(targetLang) ? (
            <span
              className="badge"
              style={{
                background: 'linear-gradient(135deg, #4f46e5, #7c3aed)',
                color: '#ffffff',
                border: 'none',
                fontWeight: 700
              }}
            >
              Gemma Active • {targetLang.toUpperCase()}
            </span>
          ) : !isLanguageSupportedByGemma(targetLang) ? (
            <span
              className="badge"
              style={{
                background: 'linear-gradient(135deg, #059669, #10b981)',
                color: '#ffffff',
                border: 'none',
                fontWeight: 700
              }}
            >
              Pure Bodhan IndicTrans2 Active
            </span>
          ) : (
            <span className="badge badge-gray">Disabled</span>
          )}
        </div>
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ fontSize: '0.84rem', color: 'var(--ink-2)', lineHeight: 1.5 }}>
            Synthesizes conversational, contextual visual copilot speech for supported high-resource languages.
            For unique scripts and low-resource languages (Santali, Manipuri, Kashmiri, Sindhi, Bodo, Sanskrit, Maithili, etc.),
            LLMs are automatically bypassed in favor of 100% authentic Bodhan Sovereign Indic models (IndicTrans2 &amp; Indic-Speak TTS).
          </div>

          {!isLanguageSupportedByGemma(targetLang) && (
            <div
              style={{
                backgroundColor: 'rgba(16, 185, 129, 0.08)',
                border: '1px solid rgba(16, 185, 129, 0.3)',
                borderRadius: 'var(--r-md)',
                padding: '10px 14px',
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                fontSize: '0.82rem',
                color: 'var(--ink)'
              }}
            >
              <ShieldCheck size={20} color="#059669" style={{ flexShrink: 0 }} />
              <div>
                <strong>Pure Bodhan Sovereign Indic Pipeline Active for {selectedLangObj.name} ({selectedLangObj.nativeName}):</strong>
                <div style={{ fontSize: '0.74rem', color: 'var(--ink-2)', marginTop: '2px' }}>
                  External LLMs are bypassed to protect this unique script from hallucination. All translations and speech are handled directly by Bodhan IndicTrans2 and Indic-Speak TTS.
                </div>
              </div>
            </div>
          )}

          {/* Master Toggle */}
          <div
            onClick={() => setEnableGemmaSummarizer(!enableGemmaSummarizer)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              cursor: 'pointer',
              userSelect: 'none',
              padding: '10px 14px',
              backgroundColor: 'var(--sunken)',
              borderRadius: 'var(--r-md)',
              border: '1px solid var(--rule)'
            }}
          >
            {enableGemmaSummarizer ? (
              <CheckSquare size={18} color="#6366f1" />
            ) : (
              <Square size={18} color="var(--ink-3)" />
            )}
            <div>
              <div style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--ink)' }}>
                Enable Gemma Conversational Summarizer (All Languages)
              </div>
              <div style={{ fontSize: '0.74rem', color: 'var(--ink-3)' }}>
                Synthesizes conversational speech for highways, transit stations, and shops in {selectedLangObj.name}
              </div>
            </div>
          </div>

          {enableGemmaSummarizer && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', paddingTop: '4px' }}>
              {/* Provider Selection */}
              <div>
                <label style={{ display: 'block', fontSize: '0.84rem', fontWeight: 600, marginBottom: '6px' }}>
                  Inference Provider:
                </label>
                <select
                  value={gemmaProvider}
                  onChange={(e) => setGemmaProvider(e.target.value as any)}
                  className="select-control"
                >
                  <option value="builtin">Smart Built-in Gemma Engine (Zero-Setup / All 11 Languages Local)</option>
                  <option value="groq">Groq Cloud API (llama-3.1-8b-instant — Free &amp; Ultra-Fast)</option>
                  <option value="openrouter">OpenRouter API (google/gemma-2-9b-it)</option>
                  <option value="custom">Custom OpenAI-compatible Endpoint</option>
                </select>
              </div>

              {/* API Key input if external provider */}
              {gemmaProvider !== 'builtin' && (
                <>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.84rem', fontWeight: 600, marginBottom: '6px' }}>
                      {gemmaProvider === 'groq'
                        ? 'Groq API Key (gsk_...)'
                        : gemmaProvider === 'openrouter'
                        ? 'OpenRouter API Key (sk-or-...)'
                        : 'API Key:'}
                    </label>
                    <div style={{ position: 'relative' }}>
                      <input
                        type={showGemmaKey ? 'text' : 'password'}
                        value={gemmaApiKey}
                        onChange={(e) => setGemmaApiKey(e.target.value)}
                        placeholder={
                          gemmaProvider === 'groq'
                            ? 'gsk_...'
                            : gemmaProvider === 'openrouter'
                            ? 'sk-or-v1-...'
                            : 'Enter API key'
                        }
                        className="input-control"
                        style={{ paddingRight: '42px', fontFamily: showGemmaKey ? 'inherit' : 'monospace' }}
                      />
                      <button
                        type="button"
                        onClick={() => setShowGemmaKey(!showGemmaKey)}
                        style={{
                          position: 'absolute',
                          right: '8px',
                          top: '50%',
                          transform: 'translateY(-50%)',
                          background: 'none',
                          border: 'none',
                          color: 'var(--ink-3)',
                          cursor: 'pointer',
                          padding: '4px'
                        }}
                      >
                        {showGemmaKey ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                  </div>

                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                      <label style={{ fontSize: '0.84rem', fontWeight: 600 }}>
                        Model ID:
                      </label>
                      <button
                        type="button"
                        onClick={handleFetchModels}
                        disabled={isFetchingModels || !gemmaApiKey}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: 'var(--brand)',
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          cursor: gemmaApiKey ? 'pointer' : 'not-allowed',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '4px'
                        }}
                      >
                        {isFetchingModels ? (
                          <>
                            <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} />
                            Fetching from Groq...
                          </>
                        ) : (
                          '🔍 Fetch Models from My Account'
                        )}
                      </button>
                    </div>

                    {availableModels.length > 0 ? (
                      <select
                        value={gemmaModel}
                        onChange={(e) => setGemmaModel(e.target.value)}
                        className="select-control"
                      >
                        {availableModels.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={gemmaModel}
                        onChange={(e) => setGemmaModel(e.target.value)}
                        placeholder="llama-3.3-70b-versatile"
                        className="input-control"
                      />
                    )}

                    {/* Quick Model Presets for Groq */}
                    {gemmaProvider === 'groq' && availableModels.length === 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '8px' }}>
                        {[
                          { id: 'openai/gpt-oss-20b', label: '⚡ GPT-OSS 20B (Active / 1000 t/s)' },
                          { id: 'openai/gpt-oss-120b', label: '🧠 GPT-OSS 120B' },
                          { id: 'qwen/qwen3.8-27b', label: '🌐 Qwen 3.8 27B' },
                          { id: 'llama-3.3-70b-versatile', label: 'Llama 3.3 70B' },
                          { id: 'llama-3.1-8b-instant', label: 'Llama 3.1 8B' }
                        ].map((preset) => (
                          <button
                            key={preset.id}
                            type="button"
                            onClick={() => setGemmaModel(preset.id)}
                            style={{
                              padding: '5px 10px',
                              borderRadius: 'var(--r-sm)',
                              fontSize: '0.74rem',
                              fontWeight: 600,
                              backgroundColor: gemmaModel === preset.id ? 'var(--brand-wash)' : 'var(--sunken)',
                              border: `1px solid ${gemmaModel === preset.id ? 'var(--brand)' : 'var(--rule)'}`,
                              color: gemmaModel === preset.id ? 'var(--brand)' : 'var(--ink-2)',
                              cursor: 'pointer'
                            }}
                          >
                            {preset.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Interactive Test Playground */}
              <div
                style={{
                  padding: '14px',
                  borderRadius: 'var(--r-md)',
                  backgroundColor: 'var(--sunken)',
                  border: '1px solid var(--rule)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '12px'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ fontSize: '0.84rem', fontWeight: 700, color: 'var(--ink)' }}>
                    🧪 Live Gemma Test Sandbox ({selectedLangObj.name})
                  </div>
                  <button
                    type="button"
                    onClick={handleTestGemma}
                    disabled={isTestingGemma}
                    className="btn btn-secondary"
                    style={{ fontSize: '0.8rem', padding: '6px 12px', gap: '6px' }}
                  >
                    {isTestingGemma ? (
                      <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
                    ) : (
                      <Sparkles size={14} color="#6366f1" />
                    )}
                    <span>{isTestingGemma ? 'Synthesizing...' : 'Run Test'}</span>
                  </button>
                </div>

                {/* Domain Selector for Testing */}
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    onClick={() => setTestDomainType('highway')}
                    className={`btn ${testDomainType === 'highway' ? 'btn-brand' : 'btn-secondary'}`}
                    style={{ fontSize: '0.78rem', padding: '6px 10px' }}
                  >
                    🛣️ Highway (NH-48)
                  </button>
                  <button
                    type="button"
                    onClick={() => setTestDomainType('railway')}
                    className={`btn ${testDomainType === 'railway' ? 'btn-brand' : 'btn-secondary'}`}
                    style={{ fontSize: '0.78rem', padding: '6px 10px' }}
                  >
                    🚆 Railway (Platform 3 &amp; 4)
                  </button>
                  <button
                    type="button"
                    onClick={() => setTestDomainType('clinic')}
                    className={`btn ${testDomainType === 'clinic' ? 'btn-brand' : 'btn-secondary'}`}
                    style={{ fontSize: '0.78rem', padding: '6px 10px' }}
                  >
                    🏥 Clinic (Agarwal Clinic)
                  </button>
                </div>

                {testOutput && (
                  <div
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px',
                      padding: '12px',
                      backgroundColor: 'var(--card)',
                      borderRadius: 'var(--r-md)',
                      border: '1px solid var(--brand)',
                      animation: 'fadeIn 0.2s ease-out'
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span className="badge badge-brand" style={{ fontSize: '0.7rem' }}>
                        {testOutput.domain}
                      </span>
                      <span style={{ fontSize: '0.7rem', color: 'var(--ink-3)' }}>
                        {testOutput.model}
                      </span>
                    </div>

                    <div>
                      <div style={{ fontSize: '0.7rem', fontWeight: 700, color: 'var(--ink-3)', textTransform: 'uppercase' }}>
                        Baseline Without Gemma (Mechanical Concatenation):
                      </div>
                      <div style={{ fontSize: '0.84rem', color: 'var(--ink-3)', fontStyle: 'italic', textDecoration: 'line-through' }}>
                        "{testOutput.raw}"
                      </div>
                    </div>

                    <div>
                      <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#6366f1', textTransform: 'uppercase', marginBottom: '2px' }}>
                        ✨ Gemma Synthesized Spoken Script:
                      </div>
                      <div style={{ fontSize: '0.94rem', fontWeight: 600, color: 'var(--ink)', lineHeight: 1.5 }}>
                        "{testOutput.gemma}"
                      </div>
                    </div>

                    <div
                      style={{
                        padding: '6px 10px',
                        backgroundColor: 'var(--sunken)',
                        borderRadius: 'var(--r-sm)',
                        fontSize: '0.76rem',
                        color: 'var(--brand-ink)',
                        fontWeight: 500
                      }}
                    >
                      🧭 <strong>Extra Context Guidance:</strong> {testOutput.tip}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Save Button */}
      <button
        onClick={handleSave}
        disabled={isSaving}
        className={saveSuccess ? 'btn btn-ok' : 'btn btn-brand'}
        style={{
          padding: '14px',
          fontSize: '0.98rem',
          fontWeight: 600,
          borderRadius: 'var(--r-lg)',
          backgroundColor: saveSuccess ? '#16a34a' : undefined,
          borderColor: saveSuccess ? '#16a34a' : undefined,
          color: '#ffffff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '10px',
          transition: 'all 0.2s ease',
          boxShadow: saveSuccess ? '0 0 16px rgba(22, 163, 74, 0.4)' : undefined,
          cursor: isSaving ? 'wait' : 'pointer'
        }}
      >
        {saveSuccess ? (
          <>
            <CheckCircle2 size={20} />
            <span>Settings &amp; Keys Saved Successfully!</span>
          </>
        ) : isSaving ? (
          <>
            <Loader2 size={20} style={{ animation: 'spin 1s linear infinite' }} />
            <span>Saving Settings...</span>
          </>
        ) : (
          <>
            <Save size={18} />
            <span>Save Settings</span>
          </>
        )}
      </button>

      {/* Floating Bottom Toast Notification */}
      {saveSuccess && (
        <div
          style={{
            position: 'fixed',
            bottom: '24px',
            left: '50%',
            transform: 'translateX(-50%)',
            backgroundColor: 'var(--card)',
            border: '2px solid #16a34a',
            color: 'var(--ink)',
            padding: '12px 24px',
            borderRadius: 'var(--r-lg)',
            boxShadow: '0 10px 30px rgba(0, 0, 0, 0.35)',
            display: 'flex',
            alignItems: 'center',
            gap: '14px',
            zIndex: 1000,
            animation: 'fadeIn 0.2s ease-out'
          }}
        >
          <div
            style={{
              width: '36px',
              height: '36px',
              borderRadius: '50%',
              backgroundColor: '#16a34a',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0
            }}
          >
            <CheckCircle2 size={22} color="#ffffff" />
          </div>
          <div>
            <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#16a34a' }}>
              Settings Saved!
            </div>
            <div style={{ fontSize: '0.78rem', color: 'var(--ink-2)' }}>
              API credentials and preferences updated in encrypted storage.
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
