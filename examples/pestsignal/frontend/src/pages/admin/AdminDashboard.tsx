import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  FileText,
  Clock,
  CheckCircle2,
  AlertTriangle,
  Flame,
  Users,
  Calendar,
  Sparkles,
  TrendingUp,
  MapPin,
  ArrowRight,
  RefreshCw,
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
import { analyticsApi, alertApi, reportApi } from '../../services/api';
import { AnalyticsOverview, Alert, Report } from '../../types';
import { useToast } from '../../context/ToastContext';

const PIE_COLORS = ['#268653', '#174D35', '#2E90FA', '#F79009', '#EE46BC', '#7A5AF8', '#12B76A'];

export const AdminDashboard: React.FC = () => {
  const { showToast } = useToast();
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [timelineData, setTimelineData] = useState<any[]>([]);
  const [cropData, setCropData] = useState<any[]>([]);
  const [regionData, setRegionData] = useState<any[]>([]);
  const [languageData, setLanguageData] = useState<any[]>([]);
  const [symptomData, setSymptomData] = useState<any[]>([]);
  const [recentAlerts, setRecentAlerts] = useState<Alert[]>([]);
  const [recentReports, setRecentReports] = useState<Report[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    loadDashboardData();
  }, []);

  const loadDashboardData = async () => {
    setIsLoading(true);
    try {
      const [
        stats,
        timeline,
        crops,
        regions,
        languages,
        symptoms,
        alerts,
        reports,
      ] = await Promise.all([
        analyticsApi.getOverview(),
        analyticsApi.getTimeline(14),
        analyticsApi.getCrops(),
        analyticsApi.getRegions(),
        analyticsApi.getLanguages(),
        analyticsApi.getSymptoms(),
        alertApi.list(),
        reportApi.list(),
      ]);

      setOverview(stats);
      setTimelineData(timeline);
      setCropData(crops);
      setRegionData(regions.slice(0, 5));
      setLanguageData(languages);
      setSymptomData(symptoms.slice(0, 5));
      setRecentAlerts(alerts.slice(0, 3));
      setRecentReports(reports.slice(0, 5));
    } catch (err) {
      console.error('Failed to load admin analytics', err);
      showToast('error', 'Analytics Error', 'Could not fetch database metrics.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="space-y-8 pb-12">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-2 border-b border-gray-200/80 gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-black text-[#174D35] tracking-tight">
            Welcome to PestSignal Command Center
          </h1>
          <p className="text-xs sm:text-sm text-[#738076] mt-0.5">
            A centralized view of agricultural observations and potential regional threats.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <button
            onClick={loadDashboardData}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-white border border-gray-200 text-xs font-semibold text-gray-700 hover:bg-gray-50 transition-colors shadow-2xs"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
            <span>Refresh Data</span>
          </button>
          <Link
            to="/admin/outbreaks"
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-red-600 text-white text-xs font-bold hover:bg-red-700 transition-colors shadow-xs"
          >
            <Flame className="w-3.5 h-3.5" />
            <span>Outbreak Engine</span>
          </Link>
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs space-y-2">
          <div className="flex items-center justify-between text-gray-500">
            <span className="text-xs font-bold uppercase tracking-wider">Total Reports</span>
            <FileText className="w-4 h-4 text-[#268653]" />
          </div>
          <div className="text-3xl font-black text-[#18251D]">{overview?.total_reports || 0}</div>
          <div className="text-[11px] text-[#738076]">Central database records</div>
        </div>

        <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs space-y-2">
          <div className="flex items-center justify-between text-gray-500">
            <span className="text-xs font-bold uppercase tracking-wider">Received Today</span>
            <Calendar className="w-4 h-4 text-blue-500" />
          </div>
          <div className="text-3xl font-black text-blue-600">{overview?.reports_today || 0}</div>
          <div className="text-[11px] text-[#738076]">Live daily influx</div>
        </div>

        <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs space-y-2">
          <div className="flex items-center justify-between text-gray-500">
            <span className="text-xs font-bold uppercase tracking-wider">Under Investigation</span>
            <Clock className="w-4 h-4 text-[#E6A23C]" />
          </div>
          <div className="text-3xl font-black text-[#E6A23C]">{overview?.reports_under_investigation || 0}</div>
          <div className="text-[11px] text-[#738076]">Assigned to field officers</div>
        </div>

        <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs space-y-2">
          <div className="flex items-center justify-between text-gray-500">
            <span className="text-xs font-bold uppercase tracking-wider">Outbreak Alerts</span>
            <Flame className="w-4 h-4 text-red-500" />
          </div>
          <div className="text-3xl font-black text-red-600">{overview?.active_alerts || 0}</div>
          <div className="text-[11px] text-red-600 font-semibold">Active cluster signals</div>
        </div>

        <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs space-y-2">
          <div className="flex items-center justify-between text-gray-500">
            <span className="text-xs font-bold uppercase tracking-wider">Resolved Reports</span>
            <CheckCircle2 className="w-4 h-4 text-emerald-600" />
          </div>
          <div className="text-3xl font-black text-emerald-700">{overview?.reports_resolved || 0}</div>
          <div className="text-[11px] text-[#738076]">Addressed & closed</div>
        </div>
      </div>

      {/* Main Charts Row 1: Timeline Volume & Crop Distribution */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Reports Timeline */}
        <div className="lg:col-span-7 bg-white p-6 rounded-3xl border border-gray-200/80 shadow-xs space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-base font-bold text-[#18251D]">Daily Reporting Influx (14 Days)</h3>
              <p className="text-xs text-[#738076]">Agricultural reports submitted across all regions</p>
            </div>
            <span className="text-[10px] font-bold text-[#268653] bg-[#EAF5EC] px-2.5 py-1 rounded-full">
              Live DB Query
            </span>
          </div>

          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={timelineData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorCount" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#268653" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#268653" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E5E7EB" />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#738076' }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#738076' }} />
                <Tooltip />
                <Area type="monotone" dataKey="count" stroke="#268653" strokeWidth={2.5} fillOpacity={1} fill="url(#colorCount)" name="Reports" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Reports by Crop */}
        <div className="lg:col-span-5 bg-white p-6 rounded-3xl border border-gray-200/80 shadow-xs space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-base font-bold text-[#18251D]">Reports by Crop</h3>
              <p className="text-xs text-[#738076]">Breakdown by commodity affected</p>
            </div>
          </div>

          <div className="h-64 w-full flex items-center justify-center">
            {cropData.length === 0 ? (
              <div className="text-xs text-gray-400">No crop observations recorded</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={cropData}
                    dataKey="count"
                    nameKey="crop"
                    cx="50%"
                    cy="50%"
                    outerRadius={80}
                    innerRadius={45}
                    paddingAngle={3}
                  >
                    {cropData.map((_, index) => (
                      <Cell key={`cell-${index}`} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>

      {/* Charts Row 2: Symptoms & Regional Breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Most Frequent Symptoms */}
        <div className="lg:col-span-6 bg-white p-6 rounded-3xl border border-gray-200/80 shadow-xs space-y-4">
          <div>
            <h3 className="text-base font-bold text-[#18251D]">Most Frequently Reported Symptoms</h3>
            <p className="text-xs text-[#738076]">Top symptoms extracted from multilingual farmer reports</p>
          </div>

          <div className="h-60 w-full">
            {symptomData.length === 0 ? (
              <div className="text-xs text-gray-400 text-center py-20">No symptoms reported yet</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={symptomData} layout="vertical" margin={{ top: 5, right: 20, left: 30, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#E5E7EB" />
                  <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} />
                  <YAxis type="category" dataKey="symptom" tick={{ fontSize: 11 }} width={100} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#268653" radius={[0, 6, 6, 0]} name="Occurrences" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* Top Districts Affected */}
        <div className="lg:col-span-6 bg-white p-6 rounded-3xl border border-gray-200/80 shadow-xs space-y-4">
          <div>
            <h3 className="text-base font-bold text-[#18251D]">Top Reporting Districts</h3>
            <p className="text-xs text-[#738076]">Geographic clusters with high volume of disease alerts</p>
          </div>

          <div className="h-60 w-full">
            {regionData.length === 0 ? (
              <div className="text-xs text-gray-400 text-center py-20">No regional data recorded</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={regionData} margin={{ top: 5, right: 10, left: -20, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E5E7EB" />
                  <XAxis dataKey="district" tick={{ fontSize: 11 }} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#174D35" radius={[6, 6, 0, 0]} name="Reports" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>

      {/* Active Outbreak Alerts Banner */}
      <div className="bg-white rounded-3xl p-6 border border-red-200 shadow-xs space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-red-100 text-red-600 flex items-center justify-center font-bold">
              <Flame className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-red-950">Active Outbreak Cluster Signals</h3>
              <p className="text-xs text-red-700/80">Identified by statistical frequency matching rules</p>
            </div>
          </div>
          <Link
            to="/admin/outbreaks"
            className="text-xs font-bold text-red-600 hover:underline flex items-center gap-1"
          >
            <span>View All Outbreaks ({recentAlerts.length})</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>

        {recentAlerts.length === 0 ? (
          <div className="p-4 bg-emerald-50 rounded-2xl text-xs text-[#174D35]">
            No active outbreak clusters detected in the selected time window.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {recentAlerts.map((alert) => (
              <div
                key={alert.id}
                className="p-4 rounded-2xl bg-red-50/50 border border-red-200/80 space-y-2 text-xs"
              >
                <div className="flex items-center justify-between font-bold">
                  <span className="text-red-900 text-sm">{alert.crop_name} in {alert.district}, {alert.state}</span>
                  <span className="px-2 py-0.5 rounded-full bg-red-600 text-white text-[10px] uppercase font-bold">
                    {alert.severity} Severity
                  </span>
                </div>
                <div className="text-gray-700">
                  <strong>Matching Reports:</strong> {alert.matching_report_count} reports in {alert.detection_period}
                </div>
                <div className="text-gray-700">
                  <strong>Symptoms:</strong> {alert.symptoms}
                </div>
                <div className="p-2 rounded-lg bg-red-100/70 text-red-800 text-[11px] font-semibold">
                  ⚠️ {alert.disclaimer}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
