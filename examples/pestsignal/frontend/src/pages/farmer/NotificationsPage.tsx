import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Bell, CheckCheck, Clock, ExternalLink } from 'lucide-react';
import { notificationApi } from '../../services/api';
import { NotificationItem } from '../../types';
import { useToast } from '../../context/ToastContext';

export const NotificationsPage: React.FC = () => {
  const { showToast } = useToast();
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    loadNotifications();
  }, []);

  const loadNotifications = async () => {
    setIsLoading(true);
    try {
      const data = await notificationApi.list();
      setNotifications(data);
    } catch (err) {
      console.error('Failed to load notifications', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleMarkAllRead = async () => {
    try {
      await notificationApi.markAllRead();
      setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
      showToast('success', 'Updated', 'All notifications marked as read.');
    } catch (err) {
      showToast('error', 'Error', 'Failed to update notifications.');
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6 pb-12">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-black text-[#174D35] tracking-tight">Notifications & Alerts</h1>
          <p className="text-xs text-[#738076]">Updates regarding your submitted crop reports</p>
        </div>
        {notifications.some((n) => !n.is_read) && (
          <button
            onClick={handleMarkAllRead}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-gray-100 hover:bg-gray-200 text-xs font-semibold text-gray-700 transition-colors"
          >
            <CheckCheck className="w-4 h-4 text-[#268653]" />
            <span>Mark all as read</span>
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="py-12 text-center text-gray-500">Loading notifications...</div>
      ) : notifications.length === 0 ? (
        <div className="bg-white rounded-3xl p-12 text-center border border-gray-200 text-gray-500">
          <Bell className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <h3 className="text-sm font-bold text-gray-700">No notifications yet</h3>
          <p className="text-xs text-gray-400 mt-1">
            Status updates and officer feedback will be delivered here.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {notifications.map((n) => (
            <div
              key={n.id}
              className={`p-4 rounded-2xl border transition-all ${
                n.is_read
                  ? 'bg-white border-gray-200/80 text-gray-700'
                  : 'bg-[#EAF5EC]/40 border-[#268653]/30 text-[#174D35]'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <h4 className="text-sm font-bold text-[#18251D]">{n.title}</h4>
                    {!n.is_read && (
                      <span className="w-2 h-2 rounded-full bg-[#268653]"></span>
                    )}
                  </div>
                  <p className="text-xs text-[#738076] leading-relaxed">{n.message}</p>
                  <div className="text-[11px] text-gray-400 flex items-center gap-1 pt-1">
                    <Clock className="w-3 h-3" />
                    <span>{new Date(n.created_at).toLocaleString()}</span>
                  </div>
                </div>

                {n.link && (
                  <Link
                    to={n.link}
                    className="shrink-0 p-2 text-[#268653] hover:bg-[#EAF5EC] rounded-lg transition-colors"
                  >
                    <ExternalLink className="w-4 h-4" />
                  </Link>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
