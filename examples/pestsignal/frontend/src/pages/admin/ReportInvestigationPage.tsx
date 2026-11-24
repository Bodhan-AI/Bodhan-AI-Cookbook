import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ArrowLeft,
  Volume2,
  Sparkles,
  CheckCircle2,
  AlertTriangle,
  Clock,
  ShieldCheck,
  Send,
  User,
  MapPin,
  Calendar,
} from 'lucide-react';
import { reportApi } from '../../services/api';
import { Report } from '../../types';
import { useToast } from '../../context/ToastContext';

export const ReportInvestigationPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const { showToast } = useToast();
  const [report, setReport] = useState<Report | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [newNote, setNewNote] = useState('');
  const [isUpdatingStatus, setIsUpdatingStatus] = useState(false);

  useEffect(() => {
    loadReport();
  }, [id]);

  const loadReport = async () => {
    if (!id) return;
    setIsLoading(true);
    try {
      const data = await reportApi.get(id);
      setReport(data);
    } catch (err: any) {
      showToast('error', 'Error', err.message || 'Could not load report.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleStatusChange = async (newStatus: string) => {
    if (!report) return;
    setIsUpdatingStatus(true);
    try {
      const updated = await reportApi.updateStatus(report.id, newStatus, newNote || undefined);
      setReport(updated);
      setNewNote('');
      showToast('success', 'Status Updated', `Report moved to '${newStatus}'`);
    } catch (err: any) {
      showToast('error', 'Update Failed', err.message || 'Could not update status');
    } finally {
      setIsUpdatingStatus(false);
    }
  };

  const handleAddNote = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!report || !newNote.trim()) return;

    try {
      await reportApi.addNote(report.id, newNote.trim());
      setNewNote('');
      showToast('success', 'Note Added', 'Investigation note logged to report history.');
      loadReport();
    } catch (err: any) {
      showToast('error', 'Failed', err.message || 'Could not add note');
    }
  };

  if (isLoading) {
    return <div className="py-12 text-center text-gray-500">Loading investigation case...</div>;
  }

  if (!report) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-500">Report not found.</p>
        <Link to="/admin/reports" className="text-xs text-[#268653] font-bold underline mt-2 block">
          Back to all reports
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-16">
      {/* Header Bar */}
      <div className="flex items-center justify-between">
        <Link
          to="/admin/reports"
          className="inline-flex items-center gap-1.5 text-xs font-bold text-[#738076] hover:text-[#18251D] transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Reports Explorer
        </Link>
        <div className="text-xs text-gray-500 font-mono">Case ID: {report.id}</div>
      </div>

      {/* Main Grid: Left Details / Right Actions */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Evidence & Data */}
        <div className="lg:col-span-8 space-y-6">
          {/* Card: Case Title & Meta */}
          <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <span className="text-xs font-mono font-bold text-[#268653] bg-[#EAF5EC] px-2.5 py-1 rounded-md">
                  {report.id}
                </span>
                <h1 className="text-2xl font-black text-[#174D35] mt-2">
                  {report.crop_name} {report.crop_variety ? `(${report.crop_variety})` : ''}
                </h1>
                <p className="text-xs text-[#738076] mt-0.5">
                  Reported by <strong>{report.farmer_name}</strong> on {report.observation_date}
                </p>
              </div>

              <span className="px-3 py-1 rounded-full text-xs font-bold bg-amber-50 text-amber-800 border border-amber-200">
                {report.investigation_status}
              </span>
            </div>

            {/* Audio Recording Player if attached */}
            {report.audio_file_path && (
              <div className="p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-[#268653] text-white flex items-center justify-center shrink-0">
                    <Volume2 className="w-5 h-5" />
                  </div>
                  <div>
                    <div className="text-xs font-bold text-[#18251D]">Original Voice Evidence</div>
                    <div className="text-[11px] text-[#738076]">Farmer audio recorded in {report.source_language.toUpperCase()}</div>
                  </div>
                </div>
                <audio controls src={report.audio_file_path} className="h-9 max-w-full sm:max-w-xs" />
              </div>
            )}

            {/* Bilingual Observation Box */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs pt-2">
              <div className="p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200 space-y-1">
                <span className="font-bold text-[#174D35] uppercase text-[10px]">
                  Original Native Speech ({report.source_language.toUpperCase()})
                </span>
                <p className="text-gray-800 leading-relaxed font-medium italic">
                  "{report.original_transcription}"
                </p>
              </div>

              <div className="p-4 rounded-2xl bg-emerald-50/50 border border-[#268653]/30 space-y-1">
                <span className="font-bold text-[#174D35] uppercase text-[10px] flex items-center gap-1">
                  <Sparkles className="w-3 h-3 text-[#268653]" />
                  English Translation (Bodhan AI)
                </span>
                <p className="text-gray-900 leading-relaxed">
                  "{report.english_translation}"
                </p>
              </div>
            </div>

            {/* Metadata Fields */}
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs pt-2">
              <div className="p-3 bg-gray-50 rounded-xl border border-gray-100">
                <span className="text-gray-400 block text-[10px] uppercase font-bold">Symptoms</span>
                <span className="font-bold text-gray-800">{report.symptoms}</span>
              </div>
              <div className="p-3 bg-gray-50 rounded-xl border border-gray-100">
                <span className="text-gray-400 block text-[10px] uppercase font-bold">Location</span>
                <span className="font-bold text-gray-800">{report.village}, {report.district}</span>
              </div>
              <div className="p-3 bg-gray-50 rounded-xl border border-gray-100">
                <span className="text-gray-400 block text-[10px] uppercase font-bold">Growth Stage</span>
                <span className="font-bold text-gray-800">{report.growth_stage || 'N/A'}</span>
              </div>
            </div>
          </div>

          {/* Card: Investigation Notes History */}
          <div className="bg-white rounded-3xl p-6 sm:p-8 border border-gray-200/80 shadow-xs space-y-4">
            <h3 className="text-base font-bold text-[#18251D] flex items-center gap-2">
              <ShieldCheck className="w-5 h-5 text-[#268653]" />
              Investigation Log & Technical Notes
            </h3>

            {report.notes && report.notes.length > 0 ? (
              <div className="space-y-3">
                {report.notes.map((note) => (
                  <div key={note.id} className="p-4 rounded-2xl bg-[#F7F9F6] border border-gray-200 text-xs space-y-1">
                    <div className="flex items-center justify-between font-bold text-gray-800">
                      <span>{note.officer_name}</span>
                      <span className="text-[11px] text-gray-400 font-normal">
                        {new Date(note.created_at).toLocaleString()}
                      </span>
                    </div>
                    <p className="text-gray-700 leading-relaxed pt-1">{note.note}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">No internal investigation notes logged yet.</p>
            )}

            {/* Add Note Form */}
            <form onSubmit={handleAddNote} className="pt-2 space-y-3">
              <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider">
                Add Technical Note / Advice
              </label>
              <textarea
                rows={3}
                value={newNote}
                onChange={(e) => setNewNote(e.target.value)}
                placeholder="Log field visit findings, suspected pest identity, or recommended chemical/organic treatment..."
                className="w-full p-3 border border-gray-300 rounded-xl text-xs focus:outline-none focus:ring-2 focus:ring-[#268653]"
              />
              <button
                type="submit"
                disabled={!newNote.trim()}
                className="px-4 py-2 bg-[#268653] text-white rounded-xl text-xs font-bold hover:bg-[#174D35] transition-colors disabled:opacity-50 flex items-center gap-1.5"
              >
                <Send className="w-3.5 h-3.5" />
                <span>Save Note to Case</span>
              </button>
            </form>
          </div>
        </div>

        {/* Right Column: Case Management Actions */}
        <div className="lg:col-span-4 space-y-6">
          <div className="bg-white rounded-3xl p-6 border border-gray-200/80 shadow-xs space-y-4">
            <h3 className="text-sm font-bold text-[#18251D] uppercase tracking-wider">
              Investigation Workflow
            </h3>
            <p className="text-xs text-[#738076]">
              Update report status to notify the farmer and update regional disease cluster stats.
            </p>

            <div className="space-y-2 pt-2">
              <button
                onClick={() => handleStatusChange('Under Review')}
                disabled={isUpdatingStatus || report.investigation_status === 'Under Review'}
                className="w-full p-3 rounded-xl border border-amber-300 bg-amber-50 hover:bg-amber-100 text-amber-900 text-xs font-bold text-left transition-colors flex items-center justify-between"
              >
                <span>Mark as Under Review</span>
                <Clock className="w-4 h-4 text-amber-600" />
              </button>

              <button
                onClick={() => handleStatusChange('Investigation')}
                disabled={isUpdatingStatus || report.investigation_status === 'Investigation'}
                className="w-full p-3 rounded-xl border border-blue-300 bg-blue-50 hover:bg-blue-100 text-blue-900 text-xs font-bold text-left transition-colors flex items-center justify-between"
              >
                <span>Dispatch Field Investigation</span>
                <ShieldCheck className="w-4 h-4 text-blue-600" />
              </button>

              <button
                onClick={() => handleStatusChange('Resolved')}
                disabled={isUpdatingStatus || report.investigation_status === 'Resolved'}
                className="w-full p-3 rounded-xl border border-emerald-300 bg-emerald-50 hover:bg-emerald-100 text-[#174D35] text-xs font-bold text-left transition-colors flex items-center justify-between"
              >
                <span>Mark as Resolved</span>
                <CheckCircle2 className="w-4 h-4 text-[#268653]" />
              </button>

              <button
                onClick={() => handleStatusChange('Closed')}
                disabled={isUpdatingStatus || report.investigation_status === 'Closed'}
                className="w-full p-3 rounded-xl border border-gray-300 bg-gray-50 hover:bg-gray-100 text-gray-700 text-xs font-bold text-left transition-colors flex items-center justify-between"
              >
                <span>Close Report Case</span>
                <Clock className="w-4 h-4 text-gray-400" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
