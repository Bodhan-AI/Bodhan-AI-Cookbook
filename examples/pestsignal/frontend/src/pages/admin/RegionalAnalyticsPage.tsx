import React, { useState, useEffect } from 'react';
import {
  BarChart3,
  TrendingUp,
  Languages,
  MapPin,
  Calendar,
  Sparkles,
} from 'lucide-react';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { analyticsApi } from '../../services/api';

const COLORS = ['#268653', '#174D35', '#2E90FA', '#F79009', '#EE46BC', '#7A5AF8', '#12B76A', '#F04438'];

export const RegionalAnalyticsPage: React.FC = () => {
  const [crops, setCrops] = useState<any[]>([]);
  const [regions, setRegions] = useState<any[]>([]);
  const [languages, setLanguages] = useState<any[]>([]);
  const [symptoms, setSymptoms] = useState<any[]>([]);
  const [timeline, setTimeline] = useState<any[]>([]);
  const [daysWindow, setDaysWindow] = useState(14);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      setIsLoading(true);
      try {
        const [c, r, l, s, t] = await Promise.all([
          analyticsApi.getCrops(),
          analyticsApi.getRegions(),
          analyticsApi.getLanguages(),
          analyticsApi.getSymptoms(),
          analyticsApi.getTimeline(daysWindow),
        ]);
        setCrops(c);
        setRegions(r);
        setLanguages(l);
        setSymptoms(s);
        setTimeline(t);
      } catch (err) {
        console.error('Failed to load analytics', err);
      } finally {
        setIsLoading(false);
      }
    }
    load();
  }, [daysWindow]);

  return (
    <div className="space-y-8 pb-12">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-black text-[#174D35] tracking-tight">
            Regional Agricultural Analytics
          </h1>
          <p className="text-xs sm:text-sm text-[#738076]">
            Comprehensive epidemiological trends and linguistic distribution from database queries.
          </p>
        </div>

        {/* Time Window Switcher */}
        <div className="flex items-center gap-1 bg-white p-1 rounded-xl border border-gray-200">
          {[7, 14, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDaysWindow(d)}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                daysWindow === d
                  ? 'bg-[#268653] text-white shadow-xs'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              {d} Days
            </button>
          ))}
        </div>
      </div>

      {/* Chart 1: Timeline Volume */}
      <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-4">
        <div>
          <h3 className="text-base font-bold text-[#18251D]">Reporting Volume Trend</h3>
          <p className="text-xs text-[#738076]">Daily observations received over the past {daysWindow} days</p>
        </div>
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={timeline} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#268653" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#268653" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E5E7EB" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
              <Tooltip />
              <Area type="monotone" dataKey="count" stroke="#268653" strokeWidth={2.5} fill="url(#areaGrad)" name="Reports" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Charts Grid: Languages & Crops */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Source Language Breakdown */}
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-4">
          <div>
            <h3 className="text-base font-bold text-[#18251D]">Reports by Source Language</h3>
            <p className="text-xs text-[#738076]">Linguistic distribution processed by Bodhan Indic-Transcribe</p>
          </div>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={languages}
                  dataKey="count"
                  nameKey="language"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  innerRadius={45}
                  paddingAngle={4}
                >
                  {languages.map((_, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Crops Breakdown */}
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-4">
          <div>
            <h3 className="text-base font-bold text-[#18251D]">Crop Breakdown</h3>
            <p className="text-xs text-[#738076]">Total disease and pest reports per agricultural commodity</p>
          </div>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={crops} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E5E7EB" />
                <XAxis dataKey="crop" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="count" fill="#174D35" radius={[6, 6, 0, 0]} name="Reports" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Symptoms Bar */}
      <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-4">
        <div>
          <h3 className="text-base font-bold text-[#18251D]">Symptom Frequency Distribution</h3>
          <p className="text-xs text-[#738076]">Aggregated observations extracted across all multilingual reports</p>
        </div>
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={symptoms} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E5E7EB" />
              <XAxis dataKey="symptom" tick={{ fontSize: 11 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#268653" radius={[6, 6, 0, 0]} name="Occurrences" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
};
