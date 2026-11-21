import React, { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Mic,
  FileText,
  Languages,
  CheckCircle2,
  ArrowRight,
  ArrowLeft,
  Sparkles,
  MapPin,
  Calendar,
  AlertTriangle,
  RotateCcw,
  Volume2,
  Sprout,
  Check,
  Send,
} from 'lucide-react';
import { AudioRecorderComponent } from '../../components/audio/AudioRecorderComponent';
import { aiApi, reportApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';

const SUPPORTED_LANGS = [
  { code: 'te', name: 'Telugu', local: 'తెలుగు' },
  { code: 'hi', name: 'Hindi', local: 'हिन्दी' },
  { code: 'ta', name: 'Tamil', local: 'தமிழ்' },
  { code: 'bn', name: 'Bengali', local: 'বাংলা' },
  { code: 'en', name: 'English', local: 'English' },
  { code: 'kn', name: 'Kannada', local: 'ಕನ್ನಡ' },
  { code: 'mr', name: 'Marathi', local: 'मराठी' },
  { code: 'gu', name: 'Gujarati', local: 'ગુજરાતી' },
  { code: 'pa', name: 'Punjabi', local: 'ਪੰਜਾਬੀ' },
];

const COMMON_SYMPTOMS = [
  'Yellow leaves',
  'Brown spots',
  'Leaf curling',
  'Wilting',
  'Holes in leaves',
  'Stem damage',
  'Root damage',
  'Unusual growth',
  'Insect infestation',
  'Other',
];

export const SubmitCropReport: React.FC = () => {
  const { user } = useAuth();
  const { showToast } = useToast();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // Wizard Step: 1..8
  const [currentStep, setCurrentStep] = useState<number>(1);

  // Step 1: Language
  const [selectedLanguage, setSelectedLanguage] = useState<string>(user?.preferred_language || 'te');

  // Step 2: Method
  const [reportingMethod, setReportingMethod] = useState<'voice' | 'text'>(
    searchParams.get('mode') === 'text' ? 'text' : 'voice'
  );

  // Step 3-5: AI Transcription & Translation
  const [originalTranscription, setOriginalTranscription] = useState<string>('');
  const [englishTranslation, setEnglishTranslation] = useState<string>('');
  const [audioFilePath, setAudioFilePath] = useState<string | undefined>(undefined);
  const [isTranslatingText, setIsTranslatingText] = useState(false);
  const [bodhanStatus, setBodhanStatus] = useState<any>(null);

  // Step 6: Crop Information
  const [cropName, setCropName] = useState<string>('');
  const [cropVariety, setCropVariety] = useState<string>('');
  const [growthStage, setGrowthStage] = useState<string>('Flowering stage');
  const [selectedSymptoms, setSelectedSymptoms] = useState<string[]>([]);
  const [customSymptom, setCustomSymptom] = useState<string>('');
  const [observationDate, setObservationDate] = useState<string>(
    new Date().toISOString().split('T')[0]
  );
  const [affectedPlants, setAffectedPlants] = useState<number | ''>('');

  // Step 7: Location
  const [state, setState] = useState<string>(user?.state || 'Andhra Pradesh');
  const [district, setDistrict] = useState<string>(user?.district || 'Guntur');
  const [village, setVillage] = useState<string>('Prathipadu');
  const [farmLocation, setFarmLocation] = useState<string>('North Block, Plot 4');
  const [latitude, setLatitude] = useState<number | undefined>(undefined);
  const [longitude, setLongitude] = useState<number | undefined>(undefined);
  const [isLocating, setIsLocating] = useState<boolean>(false);

  // Submission State
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [submittedReportId, setSubmittedReportId] = useState<string | null>(null);

  const activeLangObj = SUPPORTED_LANGS.find((l) => l.code === selectedLanguage) || SUPPORTED_LANGS[0];

  // Toggle Symptom Checkbox
  const toggleSymptom = (sym: string) => {
    setSelectedSymptoms((prev) =>
      prev.includes(sym) ? prev.filter((s) => s !== sym) : [...prev, sym]
    );
  };

  // Browser Geolocation
  const handleGetLocation = () => {
    if (!navigator.geolocation) {
      showToast('error', 'Geolocation Unavailable', 'Your browser does not support geolocation.');
      return;
    }
    setIsLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLatitude(pos.coords.latitude);
        setLongitude(pos.coords.longitude);
        setIsLocating(false);
        showToast('success', 'Location Detected', `GPS: ${pos.coords.latitude.toFixed(4)}, ${pos.coords.longitude.toFixed(4)}`);
      },
      (err) => {
        setIsLocating(false);
        showToast('warning', 'Location Skipped', 'Could not read GPS. You can continue with Village/District name.');
      },
      { timeout: 8000 }
    );
  };

  // Text Method Translation Trigger
  const handleTextTranslate = async () => {
    if (!originalTranscription.trim()) {
      showToast('warning', 'Empty Text', 'Please type your crop observations first.');
      return;
    }
    setIsTranslatingText(true);
    try {
      const res = await aiApi.translate(originalTranscription, selectedLanguage, 'en');
      setEnglishTranslation(res.translated_text);
      setBodhanStatus(res.bodhan_status);
      showToast('success', 'Translated', 'Text observation translated to English.');
      setCurrentStep(5);
    } catch (err: any) {
      showToast('error', 'Translation Failed', err.message || 'Could not translate text.');
    } finally {
      setIsTranslatingText(false);
    }
  };

  // Handle final submission to FastAPI backend
  const handleFinalSubmit = async () => {
    if (!cropName.trim()) {
      showToast('warning', 'Crop Name Required', 'Please specify the crop name.');
      setCurrentStep(6);
      return;
    }
    if (selectedSymptoms.length === 0 && !customSymptom.trim()) {
      showToast('warning', 'Symptoms Required', 'Please select at least one observed symptom.');
      setCurrentStep(6);
      return;
    }
    if (!state.trim() || !district.trim() || !village.trim()) {
      showToast('warning', 'Location Required', 'State, district, and village are required.');
      setCurrentStep(7);
      return;
    }

    setIsSubmitting(true);
    try {
      const payload = {
        crop_name: cropName,
        crop_variety: cropVariety || undefined,
        growth_stage: growthStage || undefined,
        symptoms: selectedSymptoms.join(', '),
        custom_symptom: customSymptom || undefined,
        source_language: selectedLanguage,
        original_transcription: originalTranscription,
        english_translation: englishTranslation || originalTranscription,
        audio_file_path: audioFilePath,
        submission_method: reportingMethod,
        state,
        district,
        village,
        farm_location: farmLocation || undefined,
        latitude,
        longitude,
        observation_date: observationDate,
        affected_plants: affectedPlants ? Number(affectedPlants) : undefined,
      };

      const result = await reportApi.create(payload);
      setSubmittedReportId(result.id);
      showToast('success', 'Report Submitted', `Report ID ${result.id} successfully recorded!`);
    } catch (err: any) {
      console.error('Report submission failed', err);
      showToast('error', 'Submission Failed', err.message || 'Could not save report to database.');
    } finally {
      setIsSubmitting(false);
    }
  };

  // SUCCESS SCREEN
  if (submittedReportId) {
    return (
      <div className="max-w-2xl mx-auto py-12 px-4 text-center">
        <div className="bg-white rounded-3xl p-8 sm:p-12 border border-[#268653]/30 shadow-xl space-y-6">
          <div className="w-20 h-20 rounded-full bg-[#EAF5EC] text-[#268653] flex items-center justify-center mx-auto">
            <CheckCircle2 className="w-12 h-12" />
          </div>

          <h2 className="text-3xl font-black text-[#174D35] tracking-tight">Report Logged Successfully!</h2>
          <p className="text-sm text-[#738076] max-w-md mx-auto">
            Your observation has been registered in the centralized agricultural database and analyzed for potential regional outbreak signals.
          </p>

          <div className="p-4 bg-[#F7F9F6] rounded-2xl border border-gray-200 inline-block text-left w-full max-w-md">
            <div className="flex justify-between items-center py-1 border-b border-gray-200/60 text-xs">
              <span className="text-[#738076]">Unique Report ID:</span>
              <span className="font-mono font-bold text-[#174D35] text-sm">{submittedReportId}</span>
            </div>
            <div className="flex justify-between items-center py-1 border-b border-gray-200/60 text-xs">
              <span className="text-[#738076]">Crop:</span>
              <span className="font-bold text-[#18251D]">{cropName}</span>
            </div>
            <div className="flex justify-between items-center py-1 border-b border-gray-200/60 text-xs">
              <span className="text-[#738076]">Region:</span>
              <span className="font-medium text-[#18251D]">{village}, {district}</span>
            </div>
            <div className="flex justify-between items-center py-1 text-xs">
              <span className="text-[#738076]">Status:</span>
              <span className="px-2 py-0.5 rounded-full bg-emerald-100 text-[#174D35] font-semibold text-[11px]">
                Submitted
              </span>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3 pt-4">
            <button
              onClick={() => navigate(`/farmer/reports/${submittedReportId}`)}
              className="w-full sm:w-auto px-6 py-3 rounded-xl bg-[#268653] text-white font-bold text-sm hover:bg-[#174D35] transition-all"
            >
              View Report Details
            </button>
            <button
              onClick={() => {
                setSubmittedReportId(null);
                setCurrentStep(1);
                setOriginalTranscription('');
                setEnglishTranslation('');
                setSelectedSymptoms([]);
                setCropName('');
              }}
              className="w-full sm:w-auto px-6 py-3 rounded-xl bg-gray-100 text-gray-700 font-bold text-sm hover:bg-gray-200 transition-all"
            >
              Submit Another Report
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8 pb-16">
      {/* Wizard Header */}
      <div className="text-center space-y-2">
        <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-[#EAF5EC] text-[#174D35] text-xs font-bold uppercase tracking-wider">
          <Sprout className="w-3.5 h-3.5 text-[#268653]" />
          Guided Multi-Step Crop Reporting
        </div>
        <h1 className="text-2xl sm:text-3xl font-black text-[#174D35] tracking-tight">
          Report Suspected Crop Disease or Pest Attack
        </h1>
        <p className="text-xs sm:text-sm text-[#738076]">
          Step {currentStep} of 8: {
            currentStep === 1 ? 'Select Language' :
            currentStep === 2 ? 'Reporting Method' :
            currentStep === 3 ? 'Record Audio' :
            currentStep === 4 ? 'Bodhan Speech-to-Text' :
            currentStep === 5 ? 'Bodhan Translation' :
            currentStep === 6 ? 'Crop & Symptoms' :
            currentStep === 7 ? 'Location Details' : 'Review & Submit'
          }
        </p>
      </div>

      {/* Step Progress Bar */}
      <div className="flex items-center justify-between gap-1 max-w-2xl mx-auto px-4">
        {[1, 2, 3, 4, 5, 6, 7, 8].map((s) => (
          <div
            key={s}
            className={`h-2 flex-1 rounded-full transition-all ${
              s <= currentStep ? 'bg-[#268653]' : 'bg-gray-200'
            }`}
          />
        ))}
      </div>

      {/* STEP 1: SELECT LANGUAGE */}
      {currentStep === 1 && (
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-6">
          <div>
            <h3 className="text-lg font-bold text-[#18251D]">Select Your Preferred Language</h3>
            <p className="text-xs text-[#738076]">
              Choose the language you will speak or type in. Bodhan AI Indic models will transcribe and translate your voice.
            </p>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3.5">
            {SUPPORTED_LANGS.map((lang) => (
              <button
                key={lang.code}
                type="button"
                onClick={() => setSelectedLanguage(lang.code)}
                className={`p-4 rounded-2xl border text-left flex items-center justify-between transition-all ${
                  selectedLanguage === lang.code
                    ? 'border-[#268653] bg-[#EAF5EC] text-[#174D35] ring-2 ring-[#268653]/30 font-bold'
                    : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50 text-gray-700'
                }`}
              >
                <div>
                  <div className="text-sm font-semibold">{lang.name}</div>
                  <div className="text-xs text-[#738076]">{lang.local}</div>
                </div>
                {selectedLanguage === lang.code && (
                  <Check className="w-5 h-5 text-[#268653]" />
                )}
              </button>
            ))}
          </div>

          <div className="flex justify-end pt-4">
            <button
              onClick={() => setCurrentStep(2)}
              className="flex items-center gap-2 px-6 py-3 rounded-xl bg-[#268653] text-white font-bold text-sm hover:bg-[#174D35] transition-all"
            >
              <span>Next: Select Method</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* STEP 2: SELECT METHOD */}
      {currentStep === 2 && (
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-6">
          <div>
            <h3 className="text-lg font-bold text-[#18251D]">Choose Reporting Method</h3>
            <p className="text-xs text-[#738076]">
              How would you like to describe the crop problem?
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <button
              type="button"
              onClick={() => {
                setReportingMethod('voice');
                setCurrentStep(3);
              }}
              className={`p-6 rounded-2xl border text-left flex items-start gap-4 transition-all hover:scale-[1.01] ${
                reportingMethod === 'voice'
                  ? 'border-[#268653] bg-[#EAF5EC] shadow-sm'
                  : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-[#174D35] to-[#268653] text-white flex items-center justify-center shrink-0">
                <Mic className="w-6 h-6" />
              </div>
              <div>
                <h4 className="text-base font-bold text-[#174D35]">Voice Reporting (Recommended)</h4>
                <p className="text-xs text-[#738076] mt-1 leading-relaxed">
                  Describe the problem using your voice in {activeLangObj.name}. Bodhan AI will transcribe and translate it automatically.
                </p>
              </div>
            </button>

            <button
              type="button"
              onClick={() => {
                setReportingMethod('text');
                setCurrentStep(4);
              }}
              className={`p-6 rounded-2xl border text-left flex items-start gap-4 transition-all hover:scale-[1.01] ${
                reportingMethod === 'text'
                  ? 'border-[#268653] bg-[#EAF5EC] shadow-sm'
                  : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <div className="w-12 h-12 rounded-2xl bg-[#EAF5EC] text-[#268653] flex items-center justify-center shrink-0">
                <FileText className="w-6 h-6" />
              </div>
              <div>
                <h4 className="text-base font-bold text-[#174D35]">Text Reporting</h4>
                <p className="text-xs text-[#738076] mt-1 leading-relaxed">
                  Type your crop observations in {activeLangObj.name} or English.
                </p>
              </div>
            </button>
          </div>

          <div className="flex justify-between pt-4">
            <button
              onClick={() => setCurrentStep(1)}
              className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl border border-gray-300 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </button>
          </div>
        </div>
      )}

      {/* STEP 3: VOICE RECORDING */}
      {currentStep === 3 && (
        <div className="space-y-6">
          <AudioRecorderComponent
            languageCode={selectedLanguage}
            languageName={activeLangObj.name}
            onTranscriptionSuccess={(orig, engl, path) => {
              setOriginalTranscription(orig);
              setEnglishTranslation(engl);
              setAudioFilePath(path);
              setCurrentStep(4);
            }}
          />

          <div className="flex justify-between">
            <button
              onClick={() => setCurrentStep(2)}
              className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl border border-gray-300 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </button>
          </div>
        </div>
      )}

      {/* STEP 4: BODHAN AI SPEECH-TO-TEXT (OR TEXT INPUT) */}
      {currentStep === 4 && (
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-6">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Sparkles className="w-4 h-4 text-[#268653]" />
              <h3 className="text-lg font-bold text-[#18251D]">
                {reportingMethod === 'voice' ? 'Bodhan AI Speech-to-Text Transcription' : 'Type Crop Observation'}
              </h3>
            </div>
            <p className="text-xs text-[#738076]">
              {reportingMethod === 'voice'
                ? `Transcribed by Bodhan Indic-Transcribe in ${activeLangObj.name}. You can edit or refine any words below.`
                : `Type observations in ${activeLangObj.name}.`}
            </p>
          </div>

          <div>
            <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-2">
              Original Language Observation ({activeLangObj.name})
            </label>
            <textarea
              rows={4}
              value={originalTranscription}
              onChange={(e) => setOriginalTranscription(e.target.value)}
              placeholder={`Describe symptoms observed on crops in ${activeLangObj.name}...`}
              className="w-full p-4 border border-gray-300 rounded-2xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
            />
          </div>

          <div className="flex justify-between pt-2">
            <button
              onClick={() => setCurrentStep(reportingMethod === 'voice' ? 3 : 2)}
              className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl border border-gray-300 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </button>

            {reportingMethod === 'voice' ? (
              <button
                onClick={() => setCurrentStep(5)}
                disabled={!originalTranscription.trim()}
                className="flex items-center gap-2 px-6 py-3 rounded-xl bg-[#268653] text-white font-bold text-sm hover:bg-[#174D35] transition-all disabled:opacity-50"
              >
                <span>Confirm & Review Translation</span>
                <ArrowRight className="w-4 h-4" />
              </button>
            ) : (
              <button
                onClick={handleTextTranslate}
                disabled={!originalTranscription.trim() || isTranslatingText}
                className="flex items-center gap-2 px-6 py-3 rounded-xl bg-[#268653] text-white font-bold text-sm hover:bg-[#174D35] transition-all disabled:opacity-50"
              >
                <Sparkles className="w-4 h-4" />
                <span>{isTranslatingText ? 'Translating...' : 'Translate to English'}</span>
              </button>
            )}
          </div>
        </div>
      )}

      {/* STEP 5: BODHAN AI TRANSLATION REVIEW */}
      {currentStep === 5 && (
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-6">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Languages className="w-5 h-5 text-[#268653]" />
              <h3 className="text-lg font-bold text-[#18251D]">Side-by-Side Multilingual Review</h3>
            </div>
            <p className="text-xs text-[#738076]">
              Both versions are stored in the centralized database so agricultural scientists can review English while preserving original regional context.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {/* Original */}
            <div className="p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-[#174D35] uppercase">
                  Original ({activeLangObj.name})
                </span>
                <span className="text-[10px] text-gray-500 font-mono">Source Script</span>
              </div>
              <textarea
                rows={4}
                value={originalTranscription}
                onChange={(e) => setOriginalTranscription(e.target.value)}
                className="w-full p-3 bg-white border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
              />
            </div>

            {/* English Translation */}
            <div className="p-4 rounded-2xl bg-emerald-50/50 border border-[#268653]/30">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-[#174D35] uppercase flex items-center gap-1">
                  <Sparkles className="w-3.5 h-3.5 text-[#268653]" />
                  English Translation
                </span>
                <span className="text-[10px] text-[#268653] font-semibold">Bodhan AI Indic-Translate</span>
              </div>
              <textarea
                rows={4}
                value={englishTranslation}
                onChange={(e) => setEnglishTranslation(e.target.value)}
                className="w-full p-3 bg-white border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
              />
            </div>
          </div>

          <div className="flex justify-between pt-2">
            <button
              onClick={() => setCurrentStep(4)}
              className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl border border-gray-300 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </button>
            <button
              onClick={() => setCurrentStep(6)}
              className="flex items-center gap-2 px-6 py-3 rounded-xl bg-[#268653] text-white font-bold text-sm hover:bg-[#174D35] transition-all"
            >
              <span>Next: Crop & Symptoms</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* STEP 6: CROP INFORMATION & SYMPTOMS */}
      {currentStep === 6 && (
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-6">
          <div>
            <h3 className="text-lg font-bold text-[#18251D]">Crop Information & Symptoms Observed</h3>
            <p className="text-xs text-[#738076]">
              Select observed symptoms to help the outbreak detection engine spot regional patterns.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
                Crop Name *
              </label>
              <input
                type="text"
                required
                value={cropName}
                onChange={(e) => setCropName(e.target.value)}
                placeholder="e.g. Cotton, Rice (Paddy), Chilli, Wheat"
                className="w-full px-3.5 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
              />
            </div>

            <div>
              <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
                Crop Variety (Optional)
              </label>
              <input
                type="text"
                value={cropVariety}
                onChange={(e) => setCropVariety(e.target.value)}
                placeholder="e.g. Bt Cotton, BPT 5204"
                className="w-full px-3.5 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
                Growth Stage (Optional)
              </label>
              <select
                value={growthStage}
                onChange={(e) => setGrowthStage(e.target.value)}
                className="w-full px-3.5 py-2.5 border border-gray-300 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-[#268653]"
              >
                <option value="Seedling / Germination">Seedling / Germination</option>
                <option value="Vegetative stage">Vegetative stage</option>
                <option value="Tillering stage">Tillering stage</option>
                <option value="Flowering stage">Flowering stage</option>
                <option value="Fruiting / Boll formation">Fruiting / Boll formation</option>
                <option value="Maturity / Harvest stage">Maturity / Harvest stage</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
                Date Noticed *
              </label>
              <input
                type="date"
                required
                value={observationDate}
                onChange={(e) => setObservationDate(e.target.value)}
                className="w-full px-3.5 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
              />
            </div>

            <div>
              <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
                Estimated Affected Plants (Optional)
              </label>
              <input
                type="number"
                value={affectedPlants}
                onChange={(e) => setAffectedPlants(e.target.value ? Number(e.target.value) : '')}
                placeholder="e.g. 100"
                className="w-full px-3.5 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
              />
            </div>
          </div>

          {/* Symptoms Checklist */}
          <div>
            <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-2">
              Select Observed Symptoms *
            </label>
            <div className="flex flex-wrap gap-2.5">
              {COMMON_SYMPTOMS.map((sym) => {
                const isSelected = selectedSymptoms.includes(sym);
                return (
                  <button
                    key={sym}
                    type="button"
                    onClick={() => toggleSymptom(sym)}
                    className={`px-3.5 py-2 rounded-xl text-xs font-semibold border transition-all ${
                      isSelected
                        ? 'border-[#268653] bg-[#EAF5EC] text-[#174D35] ring-2 ring-[#268653]/30 shadow-xs'
                        : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
                    }`}
                  >
                    {isSelected && <span className="mr-1">✓</span>}
                    {sym}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Custom symptom */}
          <div>
            <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
              Custom Symptom Description (Optional)
            </label>
            <input
              type="text"
              value={customSymptom}
              onChange={(e) => setCustomSymptom(e.target.value)}
              placeholder="e.g. Powdery white residue on undersides of upper leaves"
              className="w-full px-3.5 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
            />
          </div>

          <div className="flex justify-between pt-2">
            <button
              onClick={() => setCurrentStep(5)}
              className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl border border-gray-300 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </button>
            <button
              onClick={() => {
                if (!cropName.trim()) {
                  showToast('warning', 'Crop Name Required', 'Please enter crop name.');
                  return;
                }
                if (selectedSymptoms.length === 0 && !customSymptom.trim()) {
                  showToast('warning', 'Symptoms Required', 'Please select at least one symptom.');
                  return;
                }
                setCurrentStep(7);
              }}
              className="flex items-center gap-2 px-6 py-3 rounded-xl bg-[#268653] text-white font-bold text-sm hover:bg-[#174D35] transition-all"
            >
              <span>Next: Location Details</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* STEP 7: LOCATION */}
      {currentStep === 7 && (
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-bold text-[#18251D]">Location Information</h3>
              <p className="text-xs text-[#738076]">
                Location is essential for regional outbreak cluster detection.
              </p>
            </div>
            <button
              type="button"
              onClick={handleGetLocation}
              disabled={isLocating}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-[#EAF5EC] text-[#174D35] text-xs font-bold hover:bg-emerald-100 transition-colors shrink-0"
            >
              <MapPin className="w-4 h-4 text-[#268653]" />
              <span>{isLocating ? 'Detecting GPS...' : 'Use Browser GPS (Optional)'}</span>
            </button>
          </div>

          {latitude && longitude && (
            <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-xl text-xs text-[#174D35] flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-[#268653]" />
              <span>GPS Coordinates Acquired: {latitude.toFixed(4)}, {longitude.toFixed(4)}</span>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
                State *
              </label>
              <input
                type="text"
                required
                value={state}
                onChange={(e) => setState(e.target.value)}
                placeholder="e.g. Andhra Pradesh"
                className="w-full px-3.5 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
              />
            </div>

            <div>
              <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
                District *
              </label>
              <input
                type="text"
                required
                value={district}
                onChange={(e) => setDistrict(e.target.value)}
                placeholder="e.g. Guntur"
                className="w-full px-3.5 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
              />
            </div>

            <div>
              <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
                Village / Town *
              </label>
              <input
                type="text"
                required
                value={village}
                onChange={(e) => setVillage(e.target.value)}
                placeholder="e.g. Prathipadu"
                className="w-full px-3.5 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
              Farm Location or Landmark (Optional)
            </label>
            <input
              type="text"
              value={farmLocation}
              onChange={(e) => setFarmLocation(e.target.value)}
              placeholder="e.g. Survey No. 42/B, Near East Canal"
              className="w-full px-3.5 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
            />
          </div>

          <div className="flex justify-between pt-2">
            <button
              onClick={() => setCurrentStep(6)}
              className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl border border-gray-300 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </button>
            <button
              onClick={() => {
                if (!state.trim() || !district.trim() || !village.trim()) {
                  showToast('warning', 'Location Required', 'State, district, and village are required.');
                  return;
                }
                setCurrentStep(8);
              }}
              className="flex items-center gap-2 px-6 py-3 rounded-xl bg-[#268653] text-white font-bold text-sm hover:bg-[#174D35] transition-all"
            >
              <span>Next: Final Review</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* STEP 8: REVIEW AND SUBMIT */}
      {currentStep === 8 && (
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-6">
          <div>
            <h3 className="text-xl font-bold text-[#174D35]">Review & Confirm Crop Report</h3>
            <p className="text-xs text-[#738076]">
              Please verify all details before submitting to the central agricultural database.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
            <div className="p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200 space-y-2">
              <div className="font-bold text-[#174D35] text-sm border-b pb-1">Farmer & Crop</div>
              <div><strong>Farmer:</strong> {user?.full_name}</div>
              <div><strong>Crop:</strong> {cropName} {cropVariety ? `(${cropVariety})` : ''}</div>
              <div><strong>Growth Stage:</strong> {growthStage}</div>
              <div><strong>Symptoms:</strong> {selectedSymptoms.join(', ') || 'Custom only'}</div>
              {customSymptom && <div><strong>Custom:</strong> {customSymptom}</div>}
              <div><strong>Observed Date:</strong> {observationDate}</div>
              {affectedPlants && <div><strong>Affected Plants:</strong> {affectedPlants}</div>}
            </div>

            <div className="p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200 space-y-2">
              <div className="font-bold text-[#174D35] text-sm border-b pb-1">Location & Language</div>
              <div><strong>Location:</strong> {village}, {district}, {state}</div>
              {farmLocation && <div><strong>Landmark:</strong> {farmLocation}</div>}
              {latitude && <div><strong>GPS:</strong> {latitude.toFixed(4)}, {longitude?.toFixed(4)}</div>}
              <div><strong>Language:</strong> {activeLangObj.name} ({selectedLanguage.toUpperCase()})</div>
              <div><strong>Method:</strong> {reportingMethod === 'voice' ? 'Voice Recording' : 'Text'}</div>
            </div>
          </div>

          {/* Observations Preview */}
          <div className="p-4 rounded-2xl bg-emerald-50/40 border border-[#268653]/30 space-y-3 text-xs">
            <div>
              <span className="font-bold text-[#174D35]">Original Regional Observation ({activeLangObj.name}):</span>
              <p className="mt-1 text-gray-800 italic bg-white p-3 rounded-xl border border-gray-200">
                "{originalTranscription}"
              </p>
            </div>
            <div>
              <span className="font-bold text-[#174D35] flex items-center gap-1">
                <Sparkles className="w-3.5 h-3.5 text-[#268653]" />
                English Translation (for Agricultural Officers & Scientists):
              </span>
              <p className="mt-1 text-gray-800 bg-white p-3 rounded-xl border border-gray-200">
                "{englishTranslation}"
              </p>
            </div>
          </div>

          <div className="flex justify-between pt-4">
            <button
              onClick={() => setCurrentStep(7)}
              className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl border border-gray-300 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              <ArrowLeft className="w-4 h-4" />
              Edit Details
            </button>
            <button
              onClick={handleFinalSubmit}
              disabled={isSubmitting}
              className="flex items-center gap-2 px-8 py-3.5 rounded-xl bg-gradient-to-r from-[#174D35] to-[#268653] text-white font-black text-sm shadow-lg shadow-[#268653]/25 hover:shadow-xl active:scale-[0.99] transition-all disabled:opacity-50"
            >
              {isSubmitting ? (
                <span>Submitting to Database...</span>
              ) : (
                <>
                  <Send className="w-4 h-4" />
                  <span>Confirm & Submit Report</span>
                </>
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
