import React from 'react';
import { Settings, Globe, Bell, Volume2, Shield, Smartphone } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

export const FarmerSettings: React.FC = () => {
  const { user } = useAuth();
  const { showToast } = useToast();

  const handleSave = () => {
    showToast('success', 'Preferences Saved', 'Your farmer reporting preferences were updated.');
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6 pb-12">
      <div>
        <h1 className="text-2xl font-black text-[#174D35] tracking-tight">Farmer Settings</h1>
        <p className="text-xs text-[#738076]">Audio recording, language, and notification preferences</p>
      </div>

      <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-6">
        <div className="space-y-4">
          <div className="flex items-center justify-between pb-4 border-b border-gray-100">
            <div>
              <div className="text-sm font-bold text-[#18251D]">Default Voice Recording Quality</div>
              <div className="text-xs text-[#738076]">16kHz 16-bit PCM WAV (Bodhan AI requirement)</div>
            </div>
            <span className="text-xs font-bold text-[#268653] bg-[#EAF5EC] px-3 py-1 rounded-full">
              Optimized
            </span>
          </div>

          <div className="flex items-center justify-between pb-4 border-b border-gray-100">
            <div>
              <div className="text-sm font-bold text-[#18251D]">Automatic English Translation</div>
              <div className="text-xs text-[#738076]">Translate native voice reports to English for scientists</div>
            </div>
            <input type="checkbox" defaultChecked className="w-4 h-4 accent-[#268653]" />
          </div>

          <div className="flex items-center justify-between pb-4 border-b border-gray-100">
            <div>
              <div className="text-sm font-bold text-[#18251D]">SMS / In-App Notifications</div>
              <div className="text-xs text-[#738076]">Alert me when an agricultural officer reviews my field report</div>
            </div>
            <input type="checkbox" defaultChecked className="w-4 h-4 accent-[#268653]" />
          </div>
        </div>

        <div className="pt-2">
          <button
            onClick={handleSave}
            className="px-6 py-2.5 rounded-xl bg-[#268653] text-white font-bold text-xs hover:bg-[#174D35] transition-all"
          >
            Save Settings
          </button>
        </div>
      </div>
    </div>
  );
};
