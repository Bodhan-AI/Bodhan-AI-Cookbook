import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  Mic,
  FileText,
  PlusCircle,
  Clock,
  CheckCircle2,
  AlertCircle,
  Bell,
  ArrowRight,
  Sprout,
  Sparkles,
  MapPin,
  Calendar,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { reportApi, notificationApi } from '../../services/api';
import { Report, NotificationItem } from '../../types';

export const FarmerDashboard: React.FC = () => {
  const { user } = useAuth();
  const [reports, setReports] = useState<Report[]>([]);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function loadData() {
      try {
        const [userReports, notifs] = await Promise.all([
          reportApi.list(),
          notificationApi.list(),
        ]);
        setReports(userReports);
        setNotifications(notifs.slice(0, 3));
      } catch (err) {
        console.error('Failed to load farmer dashboard data', err);
      } finally {
        setIsLoading(false);
      }
    }
    loadData();
  }, []);

  const totalSubmitted = reports.length;
  const underReview = reports.filter((r) => r.investigation_status === 'Under Review' || r.investigation_status === 'Investigation').length;
  const resolvedCount = reports.filter((r) => r.investigation_status === 'Resolved' || r.investigation_status === 'Closed').length;

  const recentReports = reports.slice(0, 4);

  return (
    <div className="space-y-8 pb-12">
      {/* Welcome Hero Banner */}
      <div className="bg-gradient-to-r from-[#174D35] to-[#268653] rounded-3xl p-6 sm:p-10 text-white shadow-xl shadow-[#268653]/15 flex flex-col md:flex-row items-start md:items-center justify-between gap-6 relative overflow-hidden">
        <div className="space-y-2 z-10">
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white/15 text-emerald-200 text-xs font-semibold backdrop-blur-xs">
            <Sprout className="w-3.5 h-3.5 text-emerald-300" />
            <span>Kisan Dashboard • {user?.district || 'Guntur'}, {user?.state || 'AP'}</span>
          </div>
          <h1 className="text-2xl sm:text-4xl font-black tracking-tight">
            Namaste, {user?.full_name || 'Farmer'}!
          </h1>
          <p className="text-emerald-100/90 text-sm sm:text-base max-w-xl">
            How are your crops doing today? If you notice any yellow leaves, pest attacks, or abnormal damage, report it immediately in your native language.
          </p>
        </div>

        <Link
          to="/farmer/submit"
          className="z-10 shrink-0 flex items-center gap-3 px-7 py-4 rounded-2xl bg-white text-[#174D35] font-black text-base shadow-xl hover:bg-emerald-50 hover:scale-105 active:scale-95 transition-all group"
        >
          <div className="w-9 h-9 rounded-xl bg-[#268653] text-white flex items-center justify-center group-hover:scale-110 transition-transform">
            <Mic className="w-5 h-5" />
          </div>
          <span>Report a Crop Problem</span>
        </Link>

        {/* Decorative background glow */}
        <div className="absolute -right-10 -bottom-10 w-60 h-60 bg-emerald-400/10 rounded-full blur-3xl pointer-events-none"></div>
      </div>

      {/* Metrics Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
        <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs flex items-center gap-4">
          <div className="w-12 h-12 rounded-xl bg-[#EAF5EC] text-[#268653] flex items-center justify-center font-bold">
            <FileText className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-black text-[#18251D]">{totalSubmitted}</div>
            <div className="text-xs font-semibold text-[#738076] uppercase tracking-wide">Total Reports</div>
          </div>
        </div>

        <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs flex items-center gap-4">
          <div className="w-12 h-12 rounded-xl bg-amber-50 text-[#E6A23C] flex items-center justify-center font-bold">
            <Clock className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-black text-[#18251D]">{underReview}</div>
            <div className="text-xs font-semibold text-[#738076] uppercase tracking-wide">Under Review</div>
          </div>
        </div>

        <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs flex items-center gap-4">
          <div className="w-12 h-12 rounded-xl bg-emerald-50 text-[#268653] flex items-center justify-center font-bold">
            <CheckCircle2 className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-black text-[#18251D]">{resolvedCount}</div>
            <div className="text-xs font-semibold text-[#738076] uppercase tracking-wide">Resolved Reports</div>
          </div>
        </div>

        <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs flex items-center gap-4">
          <div className="w-12 h-12 rounded-xl bg-purple-50 text-purple-600 flex items-center justify-center font-bold">
            <Bell className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-black text-[#18251D]">{notifications.length}</div>
            <div className="text-xs font-semibold text-[#738076] uppercase tracking-wide">Latest Updates</div>
          </div>
        </div>
      </div>

      {/* Quick Action Grid */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold text-[#18251D] uppercase tracking-wider">Quick Actions</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Link
            to="/farmer/submit?mode=voice"
            className="p-5 rounded-2xl bg-white border border-[#268653]/30 hover:shadow-md hover:border-[#268653] transition-all flex items-center gap-4 group"
          >
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-[#174D35] to-[#268653] text-white flex items-center justify-center shrink-0 shadow-sm group-hover:scale-105 transition-transform">
              <Mic className="w-6 h-6" />
            </div>
            <div>
              <div className="font-bold text-sm text-[#18251D]">Record Voice Report</div>
              <div className="text-xs text-[#738076]">Speak in your own language</div>
            </div>
          </Link>

          <Link
            to="/farmer/submit?mode=text"
            className="p-5 rounded-2xl bg-white border border-gray-200/80 hover:shadow-md hover:border-gray-300 transition-all flex items-center gap-4 group"
          >
            <div className="w-12 h-12 rounded-2xl bg-[#EAF5EC] text-[#268653] flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
              <FileText className="w-6 h-6" />
            </div>
            <div>
              <div className="font-bold text-sm text-[#18251D]">Submit Text Report</div>
              <div className="text-xs text-[#738076]">Type observations manually</div>
            </div>
          </Link>

          <Link
            to="/farmer/reports"
            className="p-5 rounded-2xl bg-white border border-gray-200/80 hover:shadow-md hover:border-gray-300 transition-all flex items-center gap-4 group"
          >
            <div className="w-12 h-12 rounded-2xl bg-gray-100 text-gray-700 flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
              <Sprout className="w-6 h-6" />
            </div>
            <div>
              <div className="font-bold text-sm text-[#18251D]">View My Reports</div>
              <div className="text-xs text-[#738076]">Track status & officer notes</div>
            </div>
          </Link>

          <Link
            to="/farmer/notifications"
            className="p-5 rounded-2xl bg-white border border-gray-200/80 hover:shadow-md hover:border-gray-300 transition-all flex items-center gap-4 group"
          >
            <div className="w-12 h-12 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
              <Bell className="w-6 h-6" />
            </div>
            <div>
              <div className="font-bold text-sm text-[#18251D]">Check Notifications</div>
              <div className="text-xs text-[#738076]">Officer review messages</div>
            </div>
          </Link>
        </div>
      </div>

      {/* Recent Reports List */}
      <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg font-bold text-[#18251D]">Recent Crop Reports</h2>
            <p className="text-xs text-[#738076]">Your latest submitted disease and pest observations</p>
          </div>
          <Link
            to="/farmer/reports"
            className="text-xs font-bold text-[#268653] hover:underline flex items-center gap-1"
          >
            <span>View All ({reports.length})</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>

        {recentReports.length === 0 ? (
          <div className="py-12 text-center text-gray-500">
            <Sprout className="w-12 h-12 text-gray-300 mx-auto mb-3" />
            <p className="text-sm font-semibold">No crop reports submitted yet.</p>
            <p className="text-xs text-gray-400 mt-1">Tap below to submit your first observation.</p>
            <Link
              to="/farmer/submit"
              className="mt-4 inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[#268653] text-white text-xs font-bold"
            >
              <PlusCircle className="w-4 h-4" />
              Report Issue Now
            </Link>
          </div>
        ) : (
          <div className="space-y-3">
            {recentReports.map((report) => {
              let badgeColor = 'bg-gray-100 text-gray-800';
              if (report.investigation_status === 'Under Review') {
                badgeColor = 'bg-amber-100 text-amber-800 border-amber-200';
              } else if (report.investigation_status === 'Investigation') {
                badgeColor = 'bg-blue-100 text-blue-800 border-blue-200';
              } else if (report.investigation_status === 'Resolved') {
                badgeColor = 'bg-emerald-100 text-[#174D35] border-emerald-200';
              }

              return (
                <Link
                  key={report.id}
                  to={`/farmer/reports/${report.id}`}
                  className="p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200/70 hover:border-[#268653]/40 hover:bg-white hover:shadow-xs transition-all flex flex-col sm:flex-row sm:items-center justify-between gap-4 block"
                >
                  <div className="flex items-start sm:items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-white border border-gray-200 flex items-center justify-center text-[#268653] font-bold text-xs shrink-0">
                      {report.crop_name.substring(0, 2).toUpperCase()}
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-bold text-[#18251D]">{report.crop_name}</span>
                        <span className="text-xs font-mono text-gray-400">({report.id})</span>
                      </div>
                      <div className="text-xs text-[#738076] line-clamp-1 mt-0.5">
                        {report.symptoms} • {report.village}, {report.district}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center justify-between sm:justify-end gap-3 shrink-0">
                    <span className={`px-2.5 py-1 rounded-full text-xs font-semibold border ${badgeColor}`}>
                      {report.investigation_status}
                    </span>
                    <span className="text-xs text-gray-400 flex items-center gap-1">
                      <Calendar className="w-3.5 h-3.5" />
                      {report.observation_date}
                    </span>
                    <ArrowRight className="w-4 h-4 text-gray-400 hidden sm:block" />
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
