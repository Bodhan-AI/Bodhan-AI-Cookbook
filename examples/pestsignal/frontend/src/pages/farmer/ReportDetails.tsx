import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ArrowLeft,
  Calendar,
  MapPin,
  Mic,
  FileText,
  Sparkles,
  ShieldAlert,
  Clock,
  User,
  Volume2,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import { reportApi } from '../../services/api';
import { Report } from '../../types';

export const ReportDetails: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [report, setReport] = useState<Report | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadReport() {
      if (!id) return;
      setIsLoading(true);
      try {
        const data = await reportApi.get(id);
        setReport(data);
      } catch (err: any) {
        setError(err.message || 'Could not load report.');
      } finally {
        setIsLoading(false);
      }
    }
    loadReport();
  }, [id]);

  if (isLoading) {
    return <div className="py-12 text-center text-gray-500">Loading report details...</div>;
  }

  if (error || !report) {
    return (
      <div className="bg-white rounded-3xl p-8 text-center max-w-lg mx-auto border border-gray-200">
        <AlertTriangle className="w-12 h-12 text-amber-500 mx-auto mb-3" />
        <h3 className="text-lg font-bold text-gray-800">Report Unavailable</h3>
        <p className="text-xs text-gray-500 mt-1">{error || 'Report not found'}</p>
        <Link
          to="/farmer/reports"
          className="mt-4 inline-flex items-center gap-1.5 px-4 py-2 bg-[#268653] text-white text-xs font-bold rounded-xl"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to My Reports
        </Link>
      </div>
    );
  }

  let statusBadge = 'bg-gray-100 text-gray-700 border-gray-200';
  if (report.investigation_status === 'Under Review') {
    statusBadge = 'bg-amber-100 text-amber-800 border-amber-200';
  } else if (report.investigation_status === 'Investigation') {
    statusBadge = 'bg-blue-100 text-blue-800 border-blue-200';
  } else if (report.investigation_status === 'Resolved') {
    statusBadge = 'bg-emerald-100 text-[#174D35] border-emerald-200';
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-16">
      {/* Back button */}
      <div>
        <Link
          to="/farmer/reports"
          className="inline-flex items-center gap-2 text-xs font-bold text-[#738076] hover:text-[#18251D] transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Reports List
        </Link>
      </div>

      {/* Main Report Card */}
      <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 border-b border-gray-100 gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-bold text-[#268653] bg-[#EAF5EC] px-3 py-1 rounded-md">
                {report.id}
              </span>
              <span className={`text-xs font-bold px-3 py-1 rounded-full border ${statusBadge}`}>
                {report.investigation_status}
              </span>
            </div>
            <h1 className="text-2xl font-black text-[#174D35] mt-2">
              {report.crop_name} {report.crop_variety ? `(${report.crop_variety})` : ''}
            </h1>
          </div>

          <div className="text-left sm:text-right text-xs text-[#738076]">
            <div>Submitted on {new Date(report.created_at).toLocaleDateString()}</div>
            <div className="capitalize">Method: {report.submission_method}</div>
          </div>
        </div>

        {/* Audio Player if recorded */}
        {report.audio_file_path && (
          <div className="p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-[#268653] text-white flex items-center justify-center shrink-0">
                <Volume2 className="w-5 h-5" />
              </div>
              <div>
                <div className="text-xs font-bold text-[#18251D]">Voice Recording Stored</div>
                <div className="text-[11px] text-[#738076]">Original audio submitted by farmer</div>
              </div>
            </div>
            <audio controls src={report.audio_file_path} className="h-9 max-w-full sm:max-w-xs" />
          </div>
        )}

        {/* Multilingual Observations */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="p-5 rounded-2xl bg-[#F7F9F6] border border-gray-200 space-y-2">
            <div className="text-xs font-bold text-[#174D35] uppercase flex items-center justify-between">
              <span>Original Regional Language Observation</span>
              <span className="text-[10px] bg-white px-2 py-0.5 rounded border border-gray-200">
                {report.source_language.toUpperCase()}
              </span>
            </div>
            <p className="text-sm text-gray-800 leading-relaxed font-medium">
              "{report.original_transcription}"
            </p>
          </div>

          <div className="p-5 rounded-2xl bg-emerald-50/50 border border-[#268653]/30 space-y-2">
            <div className="text-xs font-bold text-[#174D35] uppercase flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5 text-[#268653]" />
                English Translation (Bodhan AI)
              </span>
              <span className="text-[10px] bg-[#268653] text-white px-2 py-0.5 rounded">
                EN
              </span>
            </div>
            <p className="text-sm text-gray-800 leading-relaxed">
              "{report.english_translation}"
            </p>
          </div>
        </div>

        {/* Detailed Metadata Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs pt-2">
          <div className="p-3.5 rounded-xl bg-gray-50 border border-gray-100">
            <span className="text-gray-400 block mb-0.5 font-medium">Observed Symptoms</span>
            <span className="font-bold text-gray-800">{report.symptoms}</span>
            {report.custom_symptom && (
              <span className="block text-gray-600 mt-1">Note: {report.custom_symptom}</span>
            )}
          </div>

          <div className="p-3.5 rounded-xl bg-gray-50 border border-gray-100">
            <span className="text-gray-400 block mb-0.5 font-medium">Location</span>
            <span className="font-bold text-gray-800">{report.village}, {report.district}, {report.state}</span>
            {report.farm_location && (
              <span className="block text-gray-600 mt-1">Landmark: {report.farm_location}</span>
            )}
          </div>

          <div className="p-3.5 rounded-xl bg-gray-50 border border-gray-100">
            <span className="text-gray-400 block mb-0.5 font-medium">Crop Growth Stage</span>
            <span className="font-bold text-gray-800">{report.growth_stage || 'Not specified'}</span>
            {report.affected_plants && (
              <span className="block text-gray-600 mt-1">Affected: ~{report.affected_plants} plants</span>
            )}
          </div>
        </div>

        {/* Officer Investigation Notes History */}
        <div className="pt-4 border-t border-gray-100">
          <h3 className="text-sm font-bold text-[#18251D] uppercase tracking-wider mb-3 flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-[#268653]" />
            Agricultural Officer Investigation Notes
          </h3>

          {report.notes && report.notes.length > 0 ? (
            <div className="space-y-3">
              {report.notes.map((note) => (
                <div key={note.id} className="p-4 rounded-2xl bg-blue-50/60 border border-blue-200/80 text-xs space-y-1">
                  <div className="flex items-center justify-between text-blue-900 font-bold">
                    <span>Officer: {note.officer_name}</span>
                    <span className="text-[11px] text-blue-700/80 font-normal">
                      {new Date(note.created_at).toLocaleString()}
                    </span>
                  </div>
                  <p className="text-blue-950 text-xs leading-relaxed pt-1">
                    {note.note}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <div className="p-4 rounded-xl bg-[#F7F9F6] border border-gray-200/60 text-xs text-[#738076]">
              Your report has been logged and queued for inspection by the local Agricultural Extension Officer. Notes and treatment advice will appear here once reviewed.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
