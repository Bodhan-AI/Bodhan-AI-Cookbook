import React, { useState, useEffect } from 'react';
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import {
  Sprout,
  PlusCircle,
  LayoutDashboard,
  FileText,
  Bell,
  User,
  Settings,
  LogOut,
  Globe,
  Menu,
  X,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { notificationApi } from '../../services/api';

export const FarmerLayout: React.FC = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [unreadCount, setUnreadCount] = useState(0);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    async function loadNotifications() {
      try {
        const notifs = await notificationApi.list();
        const unread = notifs.filter((n: any) => !n.is_read).length;
        setUnreadCount(unread);
      } catch (err) {
        console.error('Failed to load notifications', err);
      }
    }
    loadNotifications();
  }, []);

  const navItems = [
    { to: '/farmer/dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { to: '/farmer/submit', label: 'Report Issue', icon: PlusCircle, highlight: true },
    { to: '/farmer/reports', label: 'My Reports', icon: FileText },
    { to: '/farmer/notifications', label: 'Notifications', icon: Bell, badge: unreadCount },
    { to: '/farmer/profile', label: 'My Profile', icon: User },
    { to: '/farmer/settings', label: 'Settings', icon: Settings },
  ];

  return (
    <div className="min-h-screen bg-[#F7F9F6] flex flex-col">
      {/* Top Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-30 shadow-xs">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-18 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link to="/farmer/dashboard" className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-[#174D35] to-[#268653] flex items-center justify-center text-white shadow-sm">
                <Sprout className="w-5 h-5" />
              </div>
              <div>
                <span className="text-xl font-black text-[#174D35] tracking-tight">PestSignal</span>
                <span className="ml-2 px-2 py-0.5 text-[10px] font-semibold bg-[#EAF5EC] text-[#268653] rounded-full uppercase">
                  Farmer Portal
                </span>
              </div>
            </Link>

            {/* Desktop Navigation */}
            <nav className="hidden md:flex items-center gap-1 ml-4">
              {navItems.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) =>
                      `flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm font-semibold transition-all relative ${
                        item.highlight
                          ? 'bg-[#268653] text-white hover:bg-[#174D35] shadow-xs'
                          : isActive
                          ? 'bg-[#EAF5EC] text-[#174D35]'
                          : 'text-[#738076] hover:text-[#18251D] hover:bg-gray-100/80'
                      }`
                    }
                  >
                    <Icon className="w-4 h-4" />
                    <span>{item.label}</span>
                    {item.badge && item.badge > 0 ? (
                      <span className="px-1.5 py-0.2 text-[10px] font-bold rounded-full bg-red-500 text-white">
                        {item.badge}
                      </span>
                    ) : null}
                  </NavLink>
                );
              })}
            </nav>
          </div>

          {/* Right User Bar */}
          <div className="flex items-center gap-3">
            <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200/80 text-xs text-[#738076]">
              <Globe className="w-3.5 h-3.5 text-[#268653]" />
              <span className="font-medium text-[#18251D]">
                Lang: {user?.preferred_language?.toUpperCase() || 'TE'}
              </span>
            </div>

            <div className="text-right hidden sm:block">
              <div className="text-sm font-semibold text-[#18251D]">{user?.full_name}</div>
              <div className="text-xs text-[#738076]">{user?.district || 'Guntur'}, {user?.state || 'AP'}</div>
            </div>

            <button
              onClick={() => {
                logout();
                navigate('/');
              }}
              title="Logout"
              className="p-2 rounded-xl text-[#738076] hover:text-red-600 hover:bg-red-50 transition-colors"
            >
              <LogOut className="w-5 h-5" />
            </button>

            {/* Mobile menu toggle */}
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="md:hidden p-2 rounded-lg text-gray-600 hover:bg-gray-100"
            >
              {mobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
            </button>
          </div>
        </div>

        {/* Mobile Navigation Drawer */}
        {mobileMenuOpen && (
          <div className="md:hidden border-t border-gray-200 bg-white px-4 pt-2 pb-4 space-y-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  onClick={() => setMobileMenuOpen(false)}
                  className={`flex items-center justify-between px-3 py-2.5 rounded-xl text-sm font-medium ${
                    item.highlight
                      ? 'bg-[#268653] text-white'
                      : 'text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <Icon className="w-5 h-5" />
                    <span>{item.label}</span>
                  </div>
                  {item.badge && item.badge > 0 ? (
                    <span className="px-2 py-0.5 text-xs font-bold rounded-full bg-red-500 text-white">
                      {item.badge}
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </div>
        )}
      </header>

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Outlet />
      </main>

      {/* Farmer Mobile Bottom Bar */}
      <div className="md:hidden fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 z-30 flex items-center justify-around py-2 px-3 shadow-lg">
        <Link to="/farmer/dashboard" className="flex flex-col items-center text-xs text-gray-600 hover:text-[#268653]">
          <LayoutDashboard className="w-5 h-5" />
          <span>Home</span>
        </Link>
        <Link to="/farmer/submit" className="flex flex-col items-center text-xs text-[#268653] font-bold">
          <div className="w-10 h-10 -mt-5 rounded-full bg-[#268653] text-white flex items-center justify-center shadow-md">
            <PlusCircle className="w-6 h-6" />
          </div>
          <span>Report</span>
        </Link>
        <Link to="/farmer/reports" className="flex flex-col items-center text-xs text-gray-600 hover:text-[#268653]">
          <FileText className="w-5 h-5" />
          <span>Reports</span>
        </Link>
        <Link to="/farmer/notifications" className="flex flex-col items-center text-xs text-gray-600 hover:text-[#268653] relative">
          <Bell className="w-5 h-5" />
          <span>Alerts</span>
          {unreadCount > 0 && (
            <span className="absolute top-0 right-2 w-2 h-2 rounded-full bg-red-500"></span>
          )}
        </Link>
      </div>
    </div>
  );
};
