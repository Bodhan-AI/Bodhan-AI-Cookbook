import React from 'react';
import { User, MapPin, Globe, Phone, Mail, Shield, Calendar } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

export const FarmerProfile: React.FC = () => {
  const { user } = useAuth();

  return (
    <div className="max-w-2xl mx-auto space-y-6 pb-12">
      <div>
        <h1 className="text-2xl font-black text-[#174D35] tracking-tight">Farmer Profile</h1>
        <p className="text-xs text-[#738076]">Manage your agricultural credentials and contact info</p>
      </div>

      <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-6">
        <div className="flex items-center gap-4 pb-6 border-b border-gray-100">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-[#174D35] to-[#268653] text-white flex items-center justify-center font-black text-2xl">
            {user?.full_name?.charAt(0) || 'F'}
          </div>
          <div>
            <h2 className="text-lg font-bold text-[#18251D]">{user?.full_name}</h2>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="px-2 py-0.5 rounded-full bg-[#EAF5EC] text-[#268653] text-xs font-bold uppercase">
                {user?.role}
              </span>
              <span className="text-xs text-[#738076]">Member since {user?.created_at ? new Date(user.created_at).toLocaleDateString() : '2026'}</span>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          <div className="p-4 rounded-xl bg-[#F7F9F6] border border-gray-100 space-y-1">
            <span className="text-gray-400 flex items-center gap-1.5 font-medium">
              <Mail className="w-3.5 h-3.5" /> Email
            </span>
            <span className="font-bold text-gray-800 text-sm block">{user?.email}</span>
          </div>

          <div className="p-4 rounded-xl bg-[#F7F9F6] border border-gray-100 space-y-1">
            <span className="text-gray-400 flex items-center gap-1.5 font-medium">
              <Phone className="w-3.5 h-3.5" /> Phone
            </span>
            <span className="font-bold text-gray-800 text-sm block">{user?.phone || 'Not provided'}</span>
          </div>

          <div className="p-4 rounded-xl bg-[#F7F9F6] border border-gray-100 space-y-1">
            <span className="text-gray-400 flex items-center gap-1.5 font-medium">
              <MapPin className="w-3.5 h-3.5" /> Farm Region
            </span>
            <span className="font-bold text-gray-800 text-sm block">
              {user?.district || 'Guntur'}, {user?.state || 'Andhra Pradesh'}
            </span>
          </div>

          <div className="p-4 rounded-xl bg-[#F7F9F6] border border-gray-100 space-y-1">
            <span className="text-gray-400 flex items-center gap-1.5 font-medium">
              <Globe className="w-3.5 h-3.5" /> Preferred Language
            </span>
            <span className="font-bold text-gray-800 text-sm block">
              {user?.preferred_language?.toUpperCase() || 'TE'} (Bodhan AI)
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};
