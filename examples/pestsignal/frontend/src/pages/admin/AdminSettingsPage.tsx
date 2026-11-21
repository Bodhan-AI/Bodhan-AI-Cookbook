import React, { useState } from 'react';
import { Settings, Cpu, Shield, Database, CheckCircle2, KeyRound } from 'lucide-react';
import { useToast } from '../../context/ToastContext';

export const AdminSettingsPage: React.FC = () => {
  const { showToast } = useToast();
  const [minThreshold, setMinThreshold] = useState(3);
  const [windowDays, setWindowDays] = useState(14);

  const handleSave = () => {
    showToast('success', 'Outbreak Parameters Updated', `Threshold: ${minThreshold} reports within ${windowDays} days.`);
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6 pb-12">
      <div>
        <h1 className="text-2xl sm:text-3xl font-black text-[#174D35] tracking-tight">Admin & System Settings</h1>
        <p className="text-xs sm:text-sm text-[#738076]">
          Configure Bodhan AI model parameters, security, and outbreak clustering thresholds.
        </p>
      </div>

      {/* Bodhan AI Infrastructure Diagnostics */}
      <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-4">
        <div className="flex items-center gap-2">
          <Cpu className="w-5 h-5 text-[#268653]" />
          <h3 className="text-base font-bold text-[#18251D]">Bodhan AI Model Diagnostics</h3>
        </div>

        <div className="space-y-3 text-xs">
          <div className="p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200 flex items-center justify-between">
            <div>
              <span className="font-bold text-gray-800 block">indic-transcribe (ASR)</span>
              <span className="text-gray-500 font-mono">POST https://api.bodhan.ai/v1/audio/transcriptions</span>
            </div>
            <span className="px-3 py-1 rounded-full bg-emerald-100 text-[#174D35] font-bold">
              Active (200 OK)
            </span>
          </div>

          <div className="p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200 flex items-center justify-between">
            <div>
              <span className="font-bold text-gray-800 block">indic-translate (MT)</span>
              <span className="text-gray-500 font-mono">POST https://api.bodhan.ai/v1/chat/completions</span>
            </div>
            <span className="px-3 py-1 rounded-full bg-emerald-100 text-[#174D35] font-bold">
              Configured
            </span>
          </div>
        </div>
      </div>

      {/* Outbreak Thresholds */}
      <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-4">
        <div className="flex items-center gap-2">
          <Shield className="w-5 h-5 text-[#268653]" />
          <h3 className="text-base font-bold text-[#18251D]">Outbreak Clustering Thresholds</h3>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          <div>
            <label className="block text-gray-700 font-bold mb-1">
              Minimum Reports to Trigger Alert
            </label>
            <input
              type="number"
              value={minThreshold}
              onChange={(e) => setMinThreshold(Number(e.target.value))}
              className="w-full px-3 py-2 border border-gray-300 rounded-xl text-xs"
            />
          </div>

          <div>
            <label className="block text-gray-700 font-bold mb-1">
              Time Window (Days)
            </label>
            <input
              type="number"
              value={windowDays}
              onChange={(e) => setWindowDays(Number(e.target.value))}
              className="w-full px-3 py-2 border border-gray-300 rounded-xl text-xs"
            />
          </div>
        </div>

        <button
          onClick={handleSave}
          className="px-5 py-2.5 rounded-xl bg-[#268653] text-white text-xs font-bold hover:bg-[#174D35] transition-all"
        >
          Save Threshold Rules
        </button>
      </div>
    </div>
  );
};
