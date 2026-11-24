import React from 'react';
import { Link } from 'react-router-dom';
import { Sprout, Heart, Shield, Cpu, ExternalLink } from 'lucide-react';

export const Footer: React.FC = () => {
  return (
    <footer className="bg-[#174D35] text-white border-t border-emerald-900/40">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-14">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-10">
          {/* Brand */}
          <div className="space-y-4 md:col-span-1">
            <div className="flex items-center gap-2.5">
              <div className="w-10 h-10 rounded-xl bg-[#268653] flex items-center justify-center text-white">
                <Sprout className="w-5 h-5" />
              </div>
              <span className="text-2xl font-bold tracking-tight">PestSignal</span>
            </div>
            <p className="text-sm text-emerald-100/80 leading-relaxed">
              Listen to Farmers. Detect Threats. Protect Crops.
              An AI-powered multilingual agricultural early warning platform.
            </p>
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-900/60 border border-emerald-700/50 text-xs text-emerald-200">
              <Cpu className="w-3.5 h-3.5 text-emerald-400" />
              Powered by Bodhan AI Indic Models
            </div>
          </div>

          {/* Quick Links */}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-emerald-300/80 mb-4">Platform</h3>
            <ul className="space-y-2.5 text-sm text-emerald-100/70">
              <li><Link to="/farmer/submit" className="hover:text-white transition-colors">Report a Crop Issue</Link></li>
              <li><Link to="/farmer/dashboard" className="hover:text-white transition-colors">Farmer Dashboard</Link></li>
              <li><Link to="/admin/dashboard" className="hover:text-white transition-colors">Command Center</Link></li>
              <li><Link to="/admin/outbreaks" className="hover:text-white transition-colors">Outbreak Signal Engine</Link></li>
              <li><Link to="/admin/map" className="hover:text-white transition-colors">Geographical Map</Link></li>
            </ul>
          </div>

          {/* Bodhan AI Models */}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-emerald-300/80 mb-4">AI Infrastructure</h3>
            <ul className="space-y-2.5 text-sm text-emerald-100/70">
              <li className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
                <span>Indic-Transcribe (ASR)</span>
              </li>
              <li className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
                <span>Indic-Translate (MT)</span>
              </li>
              <li className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
                <span>Rule-Based Outbreak Signals</span>
              </li>
              <li className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
                <span>25+ Scheduled Indian Languages</span>
              </li>
            </ul>
          </div>

          {/* Project Details */}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-emerald-300/80 mb-4">Project & Verification</h3>
            <p className="text-sm text-emerald-100/70 leading-relaxed mb-4">
              Designed for technical internship evaluation, agricultural NGOs, and open-source demonstration.
            </p>
            <div className="text-xs text-emerald-300/90 bg-emerald-900/40 p-3 rounded-xl border border-emerald-800">
              <strong>Evaluation Disclaimer:</strong> All detected outbreak clusters indicate statistical reporting patterns and require agricultural expert field verification.
            </div>
          </div>
        </div>

        <div className="mt-12 pt-8 border-t border-emerald-900/60 flex flex-col sm:flex-row items-center justify-between text-xs text-emerald-200/60 gap-4">
          <p>© {new Date().getFullYear()} PestSignal. Open-Source Agricultural Intelligence.</p>
          <p className="flex items-center gap-1">
            Built with <Heart className="w-3.5 h-3.5 text-rose-400 fill-rose-400" /> for Indian Farmers
          </p>
        </div>
      </div>
    </footer>
  );
};
