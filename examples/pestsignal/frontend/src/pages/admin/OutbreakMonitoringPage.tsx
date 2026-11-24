import React, { useState, useEffect } from 'react';
import {
  Flame,
  AlertTriangle,
  RefreshCw,
  ShieldCheck,
  CheckCircle2,
  Calendar,
  MapPin,
  Sparkles,
  ExternalLink,
  ChevronRight,
  Filter,
} from 'lucide-react';
import { alertApi } from '../../services/api';
import { Alert } from '../../types';
import { useToast } from '../../context/ToastContext';

export const OutbreakMonitoringPage: React.FC = () => {
  const { showToast } = useToast();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isDetecting, setIsDetecting] = useState(false);
  const [statusFilter, setStatusFilter] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');

  useEffect(() => {
    loadAlerts();
  }, [statusFilter, severityFilter]);

  const loadAlerts = async () => {
    setIsLoading(true);
    try {
      const data = await alertApi.list({
        status: statusFilter || undefined,
        severity: severityFilter || undefined,
      });
      setAlerts(data);
    } catch (err) {
      console.error('Failed to load outbreak alerts', err);
      showToast('error', 'Error', 'Could not load outbreak clusters.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleRunDetection = async () => {
    setIsDetecting(true);
    try {
      showToast('info', 'Detection Engine Running', 'Analyzing geographic clusters & symptom frequencies...');
      const updatedAlerts = await alertApi.detect();
      setAlerts(updatedAlerts);
      showToast('success', 'Detection Complete', `Identified ${updatedAlerts.length} cluster patterns in database.`);
    } catch (err: any) {
      showToast('error', 'Detection Failed', err.message || 'Could not execute outbreak engine.');
    } finally {
      setIsDetecting(false);
    }
  };

  const handleUpdateStatus = async (id: string, newStatus: string) => {
    try {
      await alertApi.updateStatus(id, newStatus);
      showToast('success', 'Alert Updated', `Outbreak signal status changed to ${newStatus}`);
      loadAlerts();
    } catch (err: any) {
      showToast('error', 'Error', err.message || 'Failed to update alert');
    }
  };

  return (
    <div className="space-y-6 pb-12">
      {/* Header Banner */}
      <div className="bg-gradient-to-r from-red-950 via-[#174D35] to-[#174D35] p-6 sm:p-8 rounded-3xl text-white shadow-xl flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
        <div className="space-y-1.5">
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-red-500/20 text-red-300 text-xs font-bold uppercase tracking-wider border border-red-500/30">
            <Flame className="w-3.5 h-3.5 text-red-400" />
            <span>Statistical Outbreak Signal Detection Engine</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-black tracking-tight">
            Agricultural Outbreak Cluster Monitoring
          </h1>
          <p className="text-emerald-100/80 text-xs sm:text-sm max-w-xl">
            Correlates incoming native voice observations across geographical districts. Flags clusters when multiple reports observe identical symptoms on the same crop within 14 days.
          </p>
        </div>

        <button
          onClick={handleRunDetection}
          disabled={isDetecting}
          className="shrink-0 flex items-center gap-2 px-6 py-3.5 rounded-2xl bg-red-600 hover:bg-red-700 text-white font-bold text-xs sm:text-sm shadow-lg shadow-red-600/30 active:scale-95 transition-all cursor-pointer disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${isDetecting ? 'animate-spin' : ''}`} />
          <span>{isDetecting ? 'Scanning Database...' : 'Run Outbreak Engine Now'}</span>
        </button>
      </div>

      {/* Mandatory Expert Disclaimer Notice */}
      <div className="p-4 rounded-2xl bg-amber-50 border border-amber-200 text-amber-900 text-xs flex items-start gap-3">
        <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
        <div>
          <span className="font-bold block uppercase tracking-wide">Agronomic Verification Mandate:</span>
          This engine detects statistical clustering of reported symptoms from farmer submissions, not laboratory-confirmed plant pathology diagnoses. Every signal requires physical field inspection by certified Agricultural Extension Officers.
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-white p-4 rounded-2xl border border-gray-200/80 shadow-2xs">
        <div className="flex items-center gap-2 text-xs text-gray-500 font-bold uppercase tracking-wider">
          <Filter className="w-4 h-4" />
          <span>Filter Clusters</span>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-1.5 border border-gray-300 rounded-xl text-xs bg-white focus:outline-none focus:ring-2 focus:ring-[#268653]"
          >
            <option value="">All Statuses</option>
            <option value="Active">Active</option>
            <option value="Investigating">Investigating</option>
            <option value="Resolved">Resolved</option>
            <option value="Dismissed">Dismissed</option>
          </select>

          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            className="px-3 py-1.5 border border-gray-300 rounded-xl text-xs bg-white focus:outline-none focus:ring-2 focus:ring-[#268653]"
          >
            <option value="">All Severities</option>
            <option value="High">High Severity</option>
            <option value="Medium">Medium Severity</option>
            <option value="Low">Low Severity</option>
          </select>
        </div>
      </div>

      {/* Alerts Grid */}
      {isLoading ? (
        <div className="py-12 text-center text-gray-500">Loading outbreak clusters...</div>
      ) : alerts.length === 0 ? (
        <div className="bg-white rounded-3xl p-12 text-center border border-gray-200 text-gray-500">
          <ShieldCheck className="w-12 h-12 text-emerald-500 mx-auto mb-3" />
          <h3 className="text-base font-bold text-gray-800">No Active Outbreak Clusters</h3>
          <p className="text-xs text-gray-400 mt-1 max-w-sm mx-auto">
            Reporting counts across districts currently remain below triggering thresholds (minimum 3 matching observations within 14 days).
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {alerts.map((alert) => {
            let severityBadge = 'bg-amber-100 text-amber-800 border-amber-300';
            if (alert.severity === 'High') {
              severityBadge = 'bg-red-600 text-white border-red-700 animate-pulse';
            } else if (alert.severity === 'Medium') {
              severityBadge = 'bg-orange-500 text-white border-orange-600';
            }

            return (
              <div
                key={alert.id}
                className="bg-white rounded-3xl p-6 border border-gray-200/80 shadow-xs hover:shadow-md transition-all flex flex-col justify-between space-y-4"
              >
                <div>
                  {/* Top Bar */}
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <span className="font-mono text-xs font-bold text-gray-500">
                      {alert.id}
                    </span>
                    <span className={`px-2.5 py-0.5 rounded-full text-[10px] uppercase font-bold border ${severityBadge}`}>
                      {alert.severity} Severity
                    </span>
                  </div>

                  {/* Title */}
                  <h3 className="text-xl font-black text-[#18251D]">
                    {alert.crop_name} Cluster in {alert.district}
                  </h3>
                  <div className="text-xs text-[#738076] flex items-center gap-1.5 mt-0.5">
                    <MapPin className="w-3.5 h-3.5 text-[#268653]" />
                    <span>{alert.district}, {alert.state}</span>
                  </div>

                  {/* Frequency Stats Box */}
                  <div className="my-3 p-3.5 rounded-2xl bg-[#F7F9F6] border border-gray-200/80 grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <span className="text-gray-400 block text-[10px] uppercase font-bold">Matching Reports</span>
                      <span className="text-base font-black text-[#174D35]">{alert.matching_report_count} Observations</span>
                    </div>
                    <div>
                      <span className="text-gray-400 block text-[10px] uppercase font-bold">Detection Period</span>
                      <span className="text-base font-black text-gray-800">{alert.detection_period}</span>
                    </div>
                  </div>

                  {/* Symptom Pattern */}
                  <div className="text-xs text-gray-700 space-y-1">
                    <span className="font-bold text-[#174D35]">Dominant Symptoms Correlated:</span>
                    <p className="bg-emerald-50/60 p-2.5 rounded-xl border border-emerald-200/60 font-medium">
                      {alert.symptoms}
                    </p>
                  </div>

                  {/* Disclaimer banner */}
                  <div className="mt-3 text-[11px] font-semibold text-amber-800 bg-amber-50 p-2.5 rounded-xl border border-amber-200">
                    ⚠️ {alert.disclaimer}
                  </div>
                </div>

                {/* Status and Action bar */}
                <div className="pt-3 border-t border-gray-100 flex items-center justify-between text-xs">
                  <div className="flex items-center gap-1">
                    <span className="text-gray-500">Status:</span>
                    <span className="font-bold text-[#18251D]">{alert.status}</span>
                  </div>

                  <div className="flex items-center gap-1.5">
                    {alert.status === 'Active' && (
                      <button
                        onClick={() => handleUpdateStatus(alert.id, 'Investigating')}
                        className="px-2.5 py-1 rounded-lg bg-blue-100 text-blue-900 font-bold text-[11px] hover:bg-blue-200"
                      >
                        Investigate
                      </button>
                    )}
                    {alert.status === 'Investigating' && (
                      <button
                        onClick={() => handleUpdateStatus(alert.id, 'Resolved')}
                        className="px-2.5 py-1 rounded-lg bg-emerald-100 text-[#174D35] font-bold text-[11px] hover:bg-emerald-200"
                      >
                        Mark Resolved
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
