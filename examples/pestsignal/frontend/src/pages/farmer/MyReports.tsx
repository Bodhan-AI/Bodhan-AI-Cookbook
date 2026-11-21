import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  Search,
  Filter,
  Calendar,
  MapPin,
  Sprout,
  ArrowRight,
  Mic,
  FileText,
  Clock,
  CheckCircle2,
  AlertCircle,
  PlusCircle,
} from 'lucide-react';
import { reportApi } from '../../services/api';
import { Report } from '../../types';

export const MyReports: React.FC = () => {
  const [reports, setReports] = useState<Report[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Filters
  const [searchTerm, setSearchTerm] = useState('');
  const [cropFilter, setCropFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  useEffect(() => {
    loadReports();
  }, [cropFilter, statusFilter]);

  const loadReports = async () => {
    setIsLoading(true);
    try {
      const data = await reportApi.list({
        crop: cropFilter || undefined,
        status: statusFilter || undefined,
        search: searchTerm || undefined,
      });
      setReports(data);
    } catch (err) {
      console.error('Failed to load farmer reports', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    loadReports();
  };

  return (
    <div className="space-y-6 pb-12">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-black text-[#174D35] tracking-tight">My Crop Reports</h1>
          <p className="text-xs sm:text-sm text-[#738076]">
            Track status, review Bodhan AI translations, and view investigation findings from agricultural officers.
          </p>
        </div>
        <Link
          to="/farmer/submit"
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[#268653] text-white font-bold text-xs sm:text-sm hover:bg-[#174D35] transition-all shrink-0"
        >
          <PlusCircle className="w-4 h-4" />
          <span>New Report</span>
        </Link>
      </div>

      {/* Filter and Search Bar */}
      <div className="bg-white p-4 sm:p-5 rounded-2xl border border-gray-200/80 shadow-xs space-y-3">
        <form onSubmit={handleSearchSubmit} className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-gray-400 absolute left-3.5 top-3.5 pointer-events-none" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search by Report ID, Crop, Symptoms..."
              className="w-full pl-10 pr-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
            />
          </div>

          <div className="flex gap-2">
            <select
              value={cropFilter}
              onChange={(e) => setCropFilter(e.target.value)}
              className="px-3 py-2.5 border border-gray-300 rounded-xl text-xs sm:text-sm bg-white focus:outline-none focus:ring-2 focus:ring-[#268653]"
            >
              <option value="">All Crops</option>
              <option value="Cotton">Cotton</option>
              <option value="Rice (Paddy)">Rice (Paddy)</option>
              <option value="Chilli">Chilli</option>
              <option value="Wheat">Wheat</option>
              <option value="Tomato">Tomato</option>
            </select>

            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-3 py-2.5 border border-gray-300 rounded-xl text-xs sm:text-sm bg-white focus:outline-none focus:ring-2 focus:ring-[#268653]"
            >
              <option value="">All Statuses</option>
              <option value="Submitted">Submitted</option>
              <option value="Under Review">Under Review</option>
              <option value="Investigation">Investigation</option>
              <option value="Resolved">Resolved</option>
              <option value="Closed">Closed</option>
            </select>

            <button
              type="submit"
              className="px-4 py-2.5 rounded-xl bg-gray-100 text-gray-700 font-semibold text-xs sm:text-sm hover:bg-gray-200 transition-colors"
            >
              Filter
            </button>
          </div>
        </form>
      </div>

      {/* Reports Grid/List */}
      {isLoading ? (
        <div className="py-12 text-center text-gray-500">Loading your reports...</div>
      ) : reports.length === 0 ? (
        <div className="bg-white rounded-3xl p-12 text-center border border-gray-200">
          <Sprout className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <h3 className="text-base font-bold text-gray-700">No matching reports found</h3>
          <p className="text-xs text-gray-400 mt-1 max-w-sm mx-auto">
            Try adjusting your search filters or submit a new crop observation.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {reports.map((report) => {
            let statusBadge = 'bg-gray-100 text-gray-700 border-gray-200';
            if (report.investigation_status === 'Under Review') {
              statusBadge = 'bg-amber-100 text-amber-800 border-amber-200';
            } else if (report.investigation_status === 'Investigation') {
              statusBadge = 'bg-blue-100 text-blue-800 border-blue-200';
            } else if (report.investigation_status === 'Resolved') {
              statusBadge = 'bg-emerald-100 text-[#174D35] border-emerald-200';
            }

            return (
              <Link
                key={report.id}
                to={`/farmer/reports/${report.id}`}
                className="bg-white rounded-2xl p-5 border border-gray-200/80 hover:border-[#268653]/40 hover:shadow-md transition-all flex flex-col justify-between group"
              >
                <div>
                  {/* Top line */}
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <span className="font-mono text-xs font-bold text-[#268653] bg-[#EAF5EC] px-2.5 py-1 rounded-md">
                      {report.id}
                    </span>
                    <span className={`text-[11px] font-bold px-2.5 py-0.5 rounded-full border ${statusBadge}`}>
                      {report.investigation_status}
                    </span>
                  </div>

                  {/* Crop Name */}
                  <h3 className="text-base font-bold text-[#18251D] group-hover:text-[#268653] transition-colors">
                    {report.crop_name} {report.crop_variety ? `(${report.crop_variety})` : ''}
                  </h3>

                  {/* Symptoms */}
                  <p className="text-xs text-[#738076] line-clamp-2 mt-1">
                    <strong>Symptoms:</strong> {report.symptoms}
                  </p>

                  {/* Translation preview */}
                  <p className="text-xs text-gray-600 italic bg-[#F7F9F6] p-2.5 rounded-xl border border-gray-100 mt-2.5 line-clamp-2">
                    "{report.english_translation}"
                  </p>
                </div>

                {/* Bottom line meta */}
                <div className="mt-4 pt-3 border-t border-gray-100 flex items-center justify-between text-xs text-[#738076]">
                  <div className="flex items-center gap-1.5">
                    <MapPin className="w-3.5 h-3.5 text-gray-400" />
                    <span>{report.village}, {report.district}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="flex items-center gap-1">
                      {report.submission_method === 'voice' ? (
                        <Mic className="w-3.5 h-3.5 text-[#268653]" />
                      ) : (
                        <FileText className="w-3.5 h-3.5 text-blue-500" />
                      )}
                      <span className="capitalize">{report.submission_method}</span>
                    </span>
                    <span>•</span>
                    <span>{report.observation_date}</span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
};
