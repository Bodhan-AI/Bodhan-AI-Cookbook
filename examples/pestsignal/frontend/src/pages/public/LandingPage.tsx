import React from 'react';
import { Link } from 'react-router-dom';
import {
  Mic,
  Languages,
  Database,
  Flame,
  Globe2,
  ArrowRight,
  ShieldCheck,
  CheckCircle2,
  Sparkles,
  Sprout,
  Users,
  Compass,
  Cpu,
} from 'lucide-react';
import { Navbar } from '../../components/common/Navbar';
import { Footer } from '../../components/common/Footer';

export const LandingPage: React.FC = () => {
  return (
    <div className="min-h-screen flex flex-col bg-[#F7F9F6]">
      <Navbar />

      {/* Hero Section */}
      <section className="relative overflow-hidden pt-12 pb-20 md:py-28 bg-gradient-to-b from-[#EAF5EC]/60 via-[#F7F9F6] to-[#F7F9F6]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
            {/* Left Column: Heading and CTAs */}
            <div className="lg:col-span-7 space-y-6 text-center lg:text-left">
              <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-[#EAF5EC] border border-[#268653]/30 text-xs font-bold text-[#174D35] uppercase tracking-wider">
                <Sparkles className="w-4 h-4 text-[#268653]" />
                Bodhan AI-Powered Multilingual Agricultural Intelligence
              </div>

              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black text-[#174D35] tracking-tight leading-[1.12]">
                Every Farmer's <br />
                <span className="text-transparent bg-clip-text bg-gradient-to-r from-[#268653] to-emerald-600">
                  Voice Matters.
                </span>
              </h1>

              <p className="text-lg sm:text-xl text-[#738076] max-w-2xl leading-relaxed">
                Report crop diseases in your own regional language. Turn local farm voice observations into structured early agricultural outbreak warnings.
              </p>

              {/* CTAs */}
              <div className="pt-2 flex flex-col sm:flex-row items-center justify-center lg:justify-start gap-4">
                <Link
                  to="/farmer/submit"
                  className="w-full sm:w-auto px-8 py-4 rounded-2xl bg-gradient-to-r from-[#174D35] to-[#268653] text-white font-bold text-base shadow-xl shadow-[#268653]/25 hover:shadow-2xl hover:-translate-y-0.5 transition-all flex items-center justify-center gap-2 group"
                >
                  <Mic className="w-5 h-5 group-hover:scale-110 transition-transform" />
                  <span>Report a Crop Issue</span>
                  <ArrowRight className="w-4 h-4" />
                </Link>

                <Link
                  to="/admin/dashboard"
                  className="w-full sm:w-auto px-8 py-4 rounded-2xl bg-white border border-[#268653]/30 text-[#174D35] font-bold text-base hover:bg-[#EAF5EC] transition-all flex items-center justify-center gap-2 shadow-xs"
                >
                  <Compass className="w-5 h-5 text-[#268653]" />
                  <span>Explore Command Center</span>
                </Link>
              </div>

              {/* Key Trust Signals */}
              <div className="pt-6 flex flex-wrap items-center justify-center lg:justify-start gap-6 text-xs text-[#738076] font-medium">
                <div className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4 text-[#268653]" />
                  <span>25+ Indian Languages</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4 text-[#268653]" />
                  <span>Voice-First AI Transcription</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4 text-[#268653]" />
                  <span>Rule-Based Outbreak Signals</span>
                </div>
              </div>
            </div>

            {/* Right Column: Visual Agricultural Showcase */}
            <div className="lg:col-span-5 relative">
              <div className="relative mx-auto max-w-md bg-white rounded-3xl p-6 shadow-2xl border border-[#268653]/20">
                {/* Card Header */}
                <div className="flex items-center justify-between pb-4 border-b border-gray-100">
                  <div className="flex items-center gap-2">
                    <div className="w-3 h-3 rounded-full bg-red-500"></div>
                    <div className="w-3 h-3 rounded-full bg-amber-500"></div>
                    <div className="w-3 h-3 rounded-full bg-emerald-500"></div>
                  </div>
                  <span className="text-[11px] font-semibold text-[#268653] bg-[#EAF5EC] px-2.5 py-1 rounded-full">
                    Live Farmer Observation
                  </span>
                </div>

                {/* Simulated Farmer Voice Card */}
                <div className="my-5 p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200/80">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-bold text-[#174D35]">Farmer Ramu • Guntur, AP</span>
                    <span className="text-[10px] text-gray-500">Telugu Audio</span>
                  </div>
                  <div className="flex items-center gap-3 bg-white p-3 rounded-xl border border-gray-100 shadow-2xs">
                    <div className="w-9 h-9 rounded-full bg-[#268653] text-white flex items-center justify-center shrink-0">
                      <Mic className="w-4 h-4" />
                    </div>
                    <div className="flex-1">
                      <div className="flex items-center gap-1">
                        <span className="h-4 w-1 bg-[#268653] rounded-full animate-pulse"></span>
                        <span className="h-6 w-1 bg-[#268653] rounded-full animate-pulse"></span>
                        <span className="h-3 w-1 bg-[#268653] rounded-full animate-pulse"></span>
                        <span className="h-7 w-1 bg-[#268653] rounded-full animate-pulse"></span>
                        <span className="h-5 w-1 bg-[#268653] rounded-full animate-pulse"></span>
                        <span className="h-8 w-1 bg-[#268653] rounded-full animate-pulse"></span>
                        <span className="h-4 w-1 bg-[#268653] rounded-full animate-pulse"></span>
                      </div>
                      <p className="text-[11px] text-[#18251D] font-medium mt-1">
                        "నా పత్తి పంటలో ఆకులపై బ్రౌన్ మచ్చలు వస్తున్నాయి..."
                      </p>
                    </div>
                  </div>
                </div>

                {/* Bodhan AI Output */}
                <div className="p-4 rounded-2xl bg-gradient-to-br from-[#174D35] to-[#268653] text-white space-y-2">
                  <div className="flex items-center justify-between text-xs text-emerald-200">
                    <span className="flex items-center gap-1">
                      <Sparkles className="w-3.5 h-3.5" />
                      Bodhan Indic-Translate
                    </span>
                    <span className="text-[10px] bg-emerald-800/80 px-2 py-0.5 rounded">English</span>
                  </div>
                  <p className="text-xs leading-relaxed font-normal text-emerald-50">
                    "In my cotton crop, brown spots are appearing on the leaves and leaves are drying and shedding."
                  </p>
                  <div className="pt-2 flex items-center justify-between text-[11px] border-t border-emerald-700/50">
                    <span className="text-emerald-300">Crop: Cotton</span>
                    <span className="bg-red-500/90 text-white font-bold px-2 py-0.5 rounded-full text-[10px]">
                      Cluster Alert Generated
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Feature Sections */}
      <section id="features" className="py-20 bg-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center max-w-3xl mx-auto mb-16">
            <h2 className="text-xs font-bold uppercase tracking-wider text-[#268653] mb-3">Platform Capabilities</h2>
            <p className="text-3xl sm:text-4xl font-extrabold text-[#174D35] tracking-tight">
              Bridging the Linguistic Divide in Agricultural Protection
            </p>
            <p className="text-sm sm:text-base text-[#738076] mt-3">
              Traditional reporting software fails farmers when language becomes a barrier. PestSignal uses sovereign Indic AI to listen, translate, and act early.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
            {/* Feature 1 */}
            <div className="p-8 rounded-3xl bg-[#F7F9F6] border border-gray-200/80 hover:shadow-lg hover:border-[#268653]/30 transition-all group">
              <div className="w-14 h-14 rounded-2xl bg-[#EAF5EC] text-[#268653] flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <Mic className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-bold text-[#174D35] mb-2">Voice-Based Reporting</h3>
              <p className="text-sm text-[#738076] leading-relaxed">
                Farmers don't need to type scientific jargon. They simply press the microphone and describe crop problems in their mother tongue naturally.
              </p>
            </div>

            {/* Feature 2 */}
            <div className="p-8 rounded-3xl bg-[#F7F9F6] border border-gray-200/80 hover:shadow-lg hover:border-[#268653]/30 transition-all group">
              <div className="w-14 h-14 rounded-2xl bg-[#EAF5EC] text-[#268653] flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <Languages className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-bold text-[#174D35] mb-2">AI-Powered Translation</h3>
              <p className="text-sm text-[#738076] leading-relaxed">
                Bodhan AI Indic-Translate converts regional language transcripts into standard English, preserving agricultural context for scientists and officers.
              </p>
            </div>

            {/* Feature 3 */}
            <div className="p-8 rounded-3xl bg-[#F7F9F6] border border-gray-200/80 hover:shadow-lg hover:border-[#268653]/30 transition-all group">
              <div className="w-14 h-14 rounded-2xl bg-[#EAF5EC] text-[#268653] flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <Database className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-bold text-[#174D35] mb-2">Centralized Reporting</h3>
              <p className="text-sm text-[#738076] leading-relaxed">
                Unified searchable database stores reports, symptom observations, audio recordings, and investigation status across all Indian districts.
              </p>
            </div>

            {/* Feature 4 */}
            <div className="p-8 rounded-3xl bg-[#F7F9F6] border border-gray-200/80 hover:shadow-lg hover:border-[#268653]/30 transition-all group">
              <div className="w-14 h-14 rounded-2xl bg-[#EAF5EC] text-[#268653] flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <Flame className="w-7 h-7 text-amber-600" />
              </div>
              <h3 className="text-xl font-bold text-[#174D35] mb-2">Early Outbreak Signals</h3>
              <p className="text-sm text-[#738076] leading-relaxed">
                Rule-based statistical clustering flags unusual spikes of similar symptoms on the same crop in the same district, enabling early field intervention.
              </p>
            </div>

            {/* Feature 5 */}
            <div className="p-8 rounded-3xl bg-[#F7F9F6] border border-gray-200/80 hover:shadow-lg hover:border-[#268653]/30 transition-all group">
              <div className="w-14 h-14 rounded-2xl bg-[#EAF5EC] text-[#268653] flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <Globe2 className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-bold text-[#174D35] mb-2">Multilingual Access</h3>
              <p className="text-sm text-[#738076] leading-relaxed">
                Full support for Telugu, Hindi, Tamil, Bengali, Kannada, Marathi, Punjabi, Gujarati, Odia, Assamese, and English.
              </p>
            </div>

            {/* Feature 6 */}
            <div className="p-8 rounded-3xl bg-[#F7F9F6] border border-gray-200/80 hover:shadow-lg hover:border-[#268653]/30 transition-all group">
              <div className="w-14 h-14 rounded-2xl bg-[#EAF5EC] text-[#268653] flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <ShieldCheck className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-bold text-[#174D35] mb-2">Role-Based Access Control</h3>
              <p className="text-sm text-[#738076] leading-relaxed">
                Strict separation between Farmers and Agricultural Officers/Admins. Farmers see only their reports; officers investigate and log expert findings.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* How It Works Section */}
      <section id="how-it-works" className="py-20 bg-[#F7F9F6]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center max-w-2xl mx-auto mb-16">
            <h2 className="text-xs font-bold uppercase tracking-wider text-[#268653] mb-3">Workflow</h2>
            <p className="text-3xl sm:text-4xl font-extrabold text-[#174D35] tracking-tight">
              From Farmer Voice to Agricultural Action in 5 Steps
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-5 gap-6">
            {[
              {
                step: '01',
                title: 'Record Observation',
                desc: 'Farmer speaks crop symptoms in their regional language using a simple one-touch microphone.',
              },
              {
                step: '02',
                title: 'Speech to Text',
                desc: 'Bodhan AI Indic-Transcribe converts speech into native text script accurately.',
              },
              {
                step: '03',
                title: 'AI Translation',
                desc: 'Indic-Translate produces English translation side-by-side with original text.',
              },
              {
                step: '04',
                title: 'Submit Report',
                desc: 'Farmer selects symptoms, confirms location, and receives a unique report tracking ID.',
              },
              {
                step: '05',
                title: 'Outbreak Detection',
                desc: 'Command Center aggregates regional data, alerts officers, and coordinates field response.',
              },
            ].map((s) => (
              <div key={s.step} className="p-6 rounded-2xl bg-white border border-gray-200/80 shadow-xs relative flex flex-col justify-between">
                <div>
                  <div className="text-3xl font-black text-[#268653]/30 mb-3">{s.step}</div>
                  <h4 className="text-base font-bold text-[#174D35] mb-2">{s.title}</h4>
                  <p className="text-xs text-[#738076] leading-relaxed">{s.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Bodhan AI Stack Section */}
      <section id="bodhan-ai" className="py-20 bg-[#174D35] text-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
            <div className="lg:col-span-6 space-y-6">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-900/60 border border-emerald-700/60 text-xs font-semibold text-emerald-300">
                <Cpu className="w-3.5 h-3.5" />
                Verified Bodhan AI Integration
              </div>
              <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight">
                Built on IIT Madras Sovereign Indic AI Models
              </h2>
              <p className="text-emerald-100/80 text-sm sm:text-base leading-relaxed">
                PestSignal integrates directly with Bodhan AI API endpoints (incubated at IIT Madras), utilizing specialized neural models optimized for Indian regional accents, dialects, and agricultural vocabulary.
              </p>
              <div className="space-y-3 pt-2">
                <div className="p-4 rounded-xl bg-emerald-900/40 border border-emerald-700/50 flex items-start gap-3">
                  <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 mt-1.5 shrink-0"></div>
                  <div>
                    <h5 className="font-bold text-sm text-emerald-100">indic-transcribe</h5>
                    <p className="text-xs text-emerald-200/70 mt-0.5">
                      OpenAI-compatible multipart audio transcription targeting <code>/v1/audio/transcriptions</code> with 16kHz PCM WAV.
                    </p>
                  </div>
                </div>
                <div className="p-4 rounded-xl bg-emerald-900/40 border border-emerald-700/50 flex items-start gap-3">
                  <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 mt-1.5 shrink-0"></div>
                  <div>
                    <h5 className="font-bold text-sm text-emerald-100">indic-translate</h5>
                    <p className="text-xs text-emerald-200/70 mt-0.5">
                      Multi-way neural translation targeting <code>/v1/chat/completions</code> connecting regional Indian tongues directly to English.
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <div className="lg:col-span-6">
              <div className="p-6 rounded-3xl bg-emerald-950/70 border border-emerald-800 text-xs font-mono space-y-4">
                <div className="flex items-center justify-between text-emerald-400 font-sans font-bold pb-2 border-b border-emerald-800">
                  <span>Backend Integration Architecture</span>
                  <span className="text-[10px] bg-emerald-800 px-2 py-0.5 rounded text-white">FastAPI 0.123+</span>
                </div>
                <pre className="text-emerald-200/90 overflow-x-auto p-3 bg-black/40 rounded-xl">
{`# Official Indic-Transcribe Call
POST https://api.bodhan.ai/v1/audio/transcriptions
Headers: { "Authorization": "Bearer sk-***" }
Body: multipart/form-data
  - file: observation.wav (16kHz PCM)
  - model: "indic-transcribe"
  - language: "te" (Telugu)

# Response Status: 200 OK
{
  "text": "వరి పైరులో ఆకులు పసుపు రంగులోకి మారి ఎండిపోతున్నాయి..."
}`}
                </pre>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* About & Demo Disclaimer */}
      <section id="about" className="py-16 bg-white border-t border-gray-100">
        <div className="max-w-4xl mx-auto px-4 text-center space-y-4">
          <h3 className="text-2xl font-bold text-[#174D35]">Internship & Open-Source Evaluation Demo</h3>
          <p className="text-sm text-[#738076] leading-relaxed">
            PestSignal is an end-to-end production-grade software prototype designed for technical evaluation. It features real FastAPI backend services, SQLite persistence, Bodhan AI speech and translation pipelines, Leaflet mapping, and statistical cluster alert generation.
          </p>
          <div className="pt-4 flex justify-center gap-4">
            <Link
              to="/register"
              className="px-6 py-3 rounded-xl bg-[#268653] text-white font-semibold text-sm hover:bg-[#174D35] transition-all"
            >
              Create Account
            </Link>
            <Link
              to="/login"
              className="px-6 py-3 rounded-xl bg-[#EAF5EC] text-[#174D35] font-semibold text-sm hover:bg-emerald-100 transition-all"
            >
              Sign In with Demo Credentials
            </Link>
          </div>
        </div>
      </section>

      <Footer />
    </div>
  );
};
