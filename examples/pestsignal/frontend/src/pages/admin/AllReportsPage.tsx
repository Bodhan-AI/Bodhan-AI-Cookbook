import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  Search,
  Filter,
  Calendar,
  MapPin,
  Mic,
  FileText,
  Volume2,
  ArrowRight,
  Shield,
  Clock,
  CheckCircle2,
  AlertTriangle,
  Languages,
} from 'lucide-react';
import { reportApi } from '../../services/api';
import { Report } from '../../types';

export const AllReportsPage: React.FC = () => {
  const [reports, setReports] = useState<Report[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Filters
  const [searchTerm, setSearchTerm] = useState('');
  const [cropFilter, setCropFilter] = useState('');
  const [districtFilter, setDistrictFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [langFilter, setLangFilter] = useState('');

  useEffect(() => {
    loadReports();
  }, [cropFilter, districtFilter, statusFilter, langFilter]);

  const loadReports = async () => {
    setIsLoading(true);
    try {
      const data = await reportApi.list({
        crop: cropFilter || undefined,
        district: districtFilter || undefined,
        status: statusFilter || undefined,
        language: langFilter || undefined,
        search: searchTerm || undefined,
      });
      setReports(data);
    } catch (err) {
      console.error('Failed to load all reports', err);
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
      <div>
        <h1 className="text-2xl sm:text-3xl font-black text-[#174D35] tracking-tight">
          All Agricultural Reports
        </h1>
        <p className="text-xs sm:text-sm text-[#738076]">
          Centralized database of farmer observations, AI translations, and investigation records.
        </p>
      </div>

      {/* Filter and Search Bar */}
      <div className="bg-white p-5 rounded-2xl border border-gray-200/80 shadow-xs space-y-3">
        <form onSubmit={handleSearchSubmit} className="space-y-3">
          <div className="relative">
            <Search className="w-4 h-4 text-gray-400 absolute left-3.5 top-3.5 pointer-events-none" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search reports by ID, crop name, symptoms, farmer observation..."
              className="w-full pl-10 pr-3 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[#268653]"
            />
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5">
            <select
              value={cropFilter}
              onChange={(e) => setCropFilter(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-xl text-xs bg-white focus:outline-none focus:ring-2 focus:ring-[#268653]"
            >
              <option value="">All Crops</option>
              <option value="Cotton">Cotton</option>
              <option value="Rice (Paddy)">Rice (Paddy)</option>
              <option value="Chilli">Chilli</option>
              <option value="Wheat">Wheat</option>
              <option value="Tomato">Tomato</option>
            </select>

            <select
              value={districtFilter}
              onChange={(e) => setDistrictFilter(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-xl text-xs bg-white focus:outline-none focus:ring-2 focus:ring-[#268653]"
            >
              <option value="">All Districts</option>
              <option value="Guntur">Guntur</option>
              <option value="Karimnagar">Karimnagar</option>
              <option value="Khammam">Khammam</option>
              <option value="Ludhiana">Ludhiana</option>
              <option value="Rajkot">Rajkot</option>
            </select>

            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-xl text-xs bg-white focus:outline-none focus:ring-2 focus:ring-[#268653]"
            >
              <option value="">All Statuses</option>
              <option value="Submitted">Submitted</option>
              <option value="Under Review">Under Review</option>
              <option value="Investigation">Investigation</option>
              <option value="Resolved">Resolved</option>
              <option value="Closed">Closed</option>
            </select>

            <select
              value={langFilter}
              onChange={(e) => setLangFilter(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-xl text-xs bg-white focus:outline-none focus:ring-2 focus:ring-[#268653]"
            >
              <option value="">All Languages</option>
              <option value="te">Telugu (TE)</option>
              <option value="hi">Hindi (HI)</option>
              <option value="ta">Tamil (TA)</option>
              <option value="bn">Bengali (BN)</option>
              <option value="en">English (EN)</option>
            </select>

            <button
              type="submit"
              className="px-4 py-2 rounded-xl bg-[#268653] text-white font-semibold text-xs hover:bg-[#174D35] transition-colors"
            >
              Apply Filter
            </button>
          </div>
        </form>
      </div>

      {/* Reports Table */}
      {isLoading ? (
        <div className="py-12 text-center text-gray-500">Loading agricultural reports...</div>
      ) : reports.length === 0 ? (
        <div className="bg-white rounded-3xl p-12 text-center border border-gray-200 text-gray-500">
          No reports matching current filter parameters.
        </div>
      ) : (
        <div className="bg-white rounded-3xl border border-gray-200/80 shadow-xs overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-[#F7F9F6] text-[#738076] font-bold uppercase tracking-wider border-b border-gray-200">
                <tr>
                  <th className="py-3.5 px-4">Report ID</th>
                  <th className="py-3.5 px-4">Crop</th>
                  <th className="py-3.5 px-4">Farmer</th>
                  <th className="py-3.5 px-4">Symptoms</th>
                  <th className="py-3.5 px-4">Location</th>
                  <th className="py-3.5 px-4">Language / Method</th>
                  <th className="py-3.5 px-4">Status</th>
                  <th className="py-3.5 px-4 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {reports.map((r) => {
                  let statusBadge = 'bg-gray-100 text-gray-700';
                  if (r.investigation_status === 'Under Review') {
                    statusBadge = 'bg-amber-100 text-amber-800';
                  } else if (r.investigation_status === 'Investigation') {
                    statusBadge = 'bg-blue-100 text-blue-800';
                  } else if (r.investigation_status === 'Resolved') {
                    statusBadge = 'bg-emerald-100 text-[#174D35]';
                  }

                  return (
                    <tr key={r.id} className="hover:bg-gray-50/80 transition-colors">
                      <td className="py-3.5 px-4 font-mono font-bold text-[#268653]">
                        {r.id}
                      </td>
                      <td className="py-3.5 px-4 font-bold text-[#18251D]">
                        {r.crop_name}
                      </td>
                      <td className="py-3.5 px-4 text-gray-700">
                        {r.farmer_name || 'Farmer'}
                      </td>
                      <td className="py-3.5 px-4 text-[#738076] max-w-xs truncate" title={r.symptoms}>
                        {r.symptoms}
                      </td>
                      <td className="py-3.5 px-4 text-gray-700">
                        {r.district}, {r.state}
                      </td>
                      <td className="py-3.5 px-4 text-gray-600">
                        <span className="uppercase font-semibold text-[10px] bg-gray-100 px-1.5 py-0.5 rounded mr-1">
                          {r.source_language}
                        </span>
                        <span className="capitalize">{r.submission_method}</span>
                      </td>
                      <td className="py-3.5 px-4">
                        <span className={`px-2.5 py-0.5 rounded-full font-semibold text-[10px] ${statusBadge}`}>
                          {r.investigation_status}
                        </span>
                      </td>
                      <td className="py-3.5 px-4 text-right">
                        <Link
                          to={`/admin/reports/${r.id}`}
                          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#EAF5EC] text-[#174D35] hover:bg-[#268653] hover:text-white font-bold transition-colors"
                        >
                          <span>Investigate</span>
                          <ArrowRight className="w-3.5 h-3.5" />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
