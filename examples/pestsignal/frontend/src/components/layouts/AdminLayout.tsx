import React, { useState, useEffect } from 'react';
import { Link, NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  Sprout,
  LayoutDashboard,
  FileSpreadsheet,
  Search,
  Flame,
  BarChart3,
  MapPin,
  Bell,
  Users,
  Settings,
  LogOut,
  AlertTriangle,
  Menu,
  X,
  ShieldCheck,
  ChevronRight
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { alertApi, notificationApi } from '../../services/api';

export const AdminLayout: React.FC = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [activeAlertsCount, setActiveAlertsCount] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    async function loadStats() {
      try {
        const [alerts, notifs] = await Promise.all([
          alertApi.list({ status: 'Active' }),
          notificationApi.list()
        ]);
        setActiveAlertsCount(alerts.length);
        setUnreadCount(notifs.filter((n: any) => !n.is_read).length);
      } catch (e) {
        console.error('Failed to load admin stats', e);
      }
    }
    loadStats();
  }, [location.pathname]);

  const navLinks = [
    { to: '/admin/dashboard', label: 'Overview', icon: LayoutDashboard },
    { to: '/admin/reports', label: 'All Reports', icon: FileSpreadsheet },
    { to: '/admin/investigations', label: 'Investigations', icon: Search },
    {
      to: '/admin/outbreaks',
      label: 'Outbreak Monitoring',
      icon: Flame,
      badge: activeAlertsCount > 0 ? activeAlertsCount : undefined,
      badgeColor: 'bg-red-500 text-white'
    },
    { to: '/admin/analytics', label: 'Regional Analytics', icon: BarChart3 },
    { to: '/admin/map', label: 'Map View', icon: MapPin },
    { to: '/admin/alerts', label: 'Alerts Management', icon: AlertTriangle },
    { to: '/admin/users', label: 'User Management', icon: Users },
    { to: '/admin/settings', label: 'Admin Settings', icon: Settings },
  ];

  return (
    <div className="min-h-screen bg-[#F7F9F6] flex">
      {/* Sidebar for Desktop */}
      <aside className="hidden lg:flex flex-col w-64 bg-[#174D35] text-white border-r border-[#174D35] shrink-0 sticky top-0 h-screen z-30">
        {/* Brand */}
        <div className="p-6 border-b border-emerald-900/60 flex items-center justify-between">
          <Link to="/admin/dashboard" className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#268653] flex items-center justify-center text-white shadow-md">
              <Sprout className="w-5 h-5" />
            </div>
            <div>
              <span className="text-xl font-black tracking-tight text-white">PestSignal</span>
              <span className="block text-[10px] uppercase font-bold text-emerald-300/80 tracking-wider">Command Center</span>
            </div>
          </Link>
        </div>

        {/* Navigation */}
        <div className="flex-1 overflow-y-auto px-3 py-6 space-y-1">
          <div className="px-3 pb-2 text-[11px] uppercase tracking-wider font-semibold text-emerald-300/60">
            Navigation
          </div>
          {navLinks.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `flex items-center justify-between px-3.5 py-2.5 rounded-xl text-sm font-medium transition-all ${
                    isActive
                      ? 'bg-[#268653] text-white shadow-xs font-semibold'
                      : 'text-emerald-100/80 hover:text-white hover:bg-emerald-800/40'
                  }`
                }
              >
                <div className="flex items-center gap-3">
                  <Icon className="w-4 h-4" />
                  <span>{item.label}</span>
                </div>
                {item.badge ? (
                  <span className={`px-2 py-0.5 text-[11px] font-bold rounded-full ${item.badgeColor}`}>
                    {item.badge}
                  </span>
                ) : null}
              </NavLink>
            );
          })}
        </div>

        {/* User profile & Logout */}
        <div className="p-4 border-t border-emerald-900/60 bg-emerald-950/30">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5 overflow-hidden">
              <div className="w-9 h-9 rounded-xl bg-emerald-700/80 flex items-center justify-center text-white shrink-0 font-bold text-xs uppercase">
                {user?.full_name?.charAt(0) || 'A'}
              </div>
              <div className="truncate text-xs">
                <div className="font-semibold text-white truncate">{user?.full_name}</div>
                <div className="text-emerald-300/70 capitalize">{user?.role}</div>
              </div>
            </div>
            <button
              onClick={() => {
                logout();
                navigate('/');
              }}
              title="Logout"
              className="p-2 text-emerald-200/70 hover:text-white hover:bg-emerald-800/60 rounded-lg transition-colors"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content Viewport */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top Navbar */}
        <header className="bg-white border-b border-gray-200 h-16 flex items-center justify-between px-4 sm:px-6 sticky top-0 z-20 shadow-2xs">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setSidebarOpen(true)}
              className="lg:hidden p-2 rounded-lg text-gray-600 hover:bg-gray-100"
            >
              <Menu className="w-6 h-6" />
            </button>
            <div className="flex items-center gap-2 text-sm text-[#738076]">
              <span className="font-semibold text-[#18251D]">Command Center</span>
              <ChevronRight className="w-4 h-4 text-gray-400" />
              <span className="capitalize">{location.pathname.split('/')[2] || 'Dashboard'}</span>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {/* Outbreak Status Pill */}
            {activeAlertsCount > 0 ? (
              <Link
                to="/admin/outbreaks"
                className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-full bg-red-50 border border-red-200 text-red-700 text-xs font-semibold hover:bg-red-100 transition-colors"
              >
                <span className="w-2 h-2 rounded-full bg-red-600 animate-ping"></span>
                <span>{activeAlertsCount} Outbreak Clusters Detected</span>
              </Link>
            ) : (
              <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-50 text-[#268653] text-xs font-medium">
                <ShieldCheck className="w-4 h-4" />
                <span>Monitoring Active</span>
              </div>
            )}

            {/* Quick Link to Farmer view */}
            <Link
              to="/farmer/dashboard"
              className="text-xs font-medium text-[#268653] hover:underline"
            >
              Switch to Farmer View
            </Link>
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1 p-4 sm:p-6 lg:p-8">
          <Outlet />
        </main>
      </div>

      {/* Mobile Sidebar Modal */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <div className="fixed inset-0 bg-black/50" onClick={() => setSidebarOpen(false)}></div>
          <div className="relative w-64 max-w-xs bg-[#174D35] text-white flex flex-col z-50">
            <div className="p-4 border-b border-emerald-900 flex items-center justify-between">
              <span className="font-bold">PestSignal Command</span>
              <button onClick={() => setSidebarOpen(false)} className="p-1 text-emerald-200">
                <X className="w-6 h-6" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-1">
              {navLinks.map((item) => {
                const Icon = item.icon;
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    onClick={() => setSidebarOpen(false)}
                    className="flex items-center justify-between px-3 py-2 rounded-lg text-sm text-emerald-100 hover:bg-emerald-800"
                  >
                    <div className="flex items-center gap-3">
                      <Icon className="w-4 h-4" />
                      <span>{item.label}</span>
                    </div>
                    {item.badge ? (
                      <span className="px-2 py-0.5 text-xs font-bold rounded-full bg-red-500 text-white">
                        {item.badge}
                      </span>
                    ) : null}
                  </Link>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
