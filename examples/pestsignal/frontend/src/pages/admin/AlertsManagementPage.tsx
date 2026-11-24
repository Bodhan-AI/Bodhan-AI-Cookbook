import React, { useState, useEffect } from 'react';
import { AlertTriangle, Flame, Shield, CheckCircle2, Clock } from 'lucide-react';
import { alertApi } from '../../services/api';
import { Alert } from '../../types';
import { useToast } from '../../context/ToastContext';

export const AlertsManagementPage: React.FC = () => {
  const { showToast } = useToast();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    loadAlerts();
  }, []);

  const loadAlerts = async () => {
    setIsLoading(true);
    try {
      const data = await alertApi.list();
      setAlerts(data);
    } catch (err) {
      console.error('Failed to load alerts', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleStatusUpdate = async (id: string, status: string) => {
    try {
      await alertApi.updateStatus(id, status);
      showToast('success', 'Status Updated', `Alert ${id} status set to ${status}`);
      loadAlerts();
    } catch (err: any) {
      showToast('error', 'Error', err.message || 'Could not update alert status.');
    }
  };

  return (
    <div className="space-y-6 pb-12">
      <div>
        <h1 className="text-2xl sm:text-3xl font-black text-[#174D35] tracking-tight">Alerts Management</h1>
        <p className="text-xs sm:text-sm text-[#738076]">
          View and manage all potential disease cluster signals detected by the outbreak engine.
        </p>
      </div>

      <div className="bg-white rounded-3xl border border-gray-200/80 shadow-xs overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-[#F7F9F6] text-[#738076] font-bold uppercase tracking-wider border-b border-gray-200">
              <tr>
                <th className="py-3.5 px-4">Alert ID</th>
                <th className="py-3.5 px-4">Crop</th>
                <th className="py-3.5 px-4">Region</th>
                <th className="py-3.5 px-4">Symptoms</th>
                <th className="py-3.5 px-4">Reports</th>
                <th className="py-3.5 px-4">Severity</th>
                <th className="py-3.5 px-4">Status</th>
                <th className="py-3.5 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {alerts.map((alt) => (
                <tr key={alt.id} className="hover:bg-gray-50/80 transition-colors">
                  <td className="py-3.5 px-4 font-mono font-bold text-red-600">{alt.id}</td>
                  <td className="py-3.5 px-4 font-bold text-gray-900">{alt.crop_name}</td>
                  <td className="py-3.5 px-4 text-gray-700">{alt.district}, {alt.state}</td>
                  <td className="py-3.5 px-4 text-[#738076] max-w-xs truncate" title={alt.symptoms}>{alt.symptoms}</td>
                  <td className="py-3.5 px-4 font-bold text-gray-800">{alt.matching_report_count}</td>
                  <td className="py-3.5 px-4">
                    <span className="px-2 py-0.5 rounded-full font-bold text-[10px] bg-red-100 text-red-800 uppercase">
                      {alt.severity}
                    </span>
                  </td>
                  <td className="py-3.5 px-4 font-semibold text-gray-800">{alt.status}</td>
                  <td className="py-3.5 px-4 text-right space-x-1">
                    <button
                      onClick={() => handleStatusUpdate(alt.id, 'Resolved')}
                      className="px-2 py-1 rounded bg-emerald-50 text-[#174D35] hover:bg-emerald-100 text-[11px] font-bold"
                    >
                      Resolve
                    </button>
                    <button
                      onClick={() => handleStatusUpdate(alt.id, 'Dismissed')}
                      className="px-2 py-1 rounded bg-gray-100 text-gray-600 hover:bg-gray-200 text-[11px] font-bold"
                    >
                      Dismiss
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
