import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Sprout, ShieldAlert, LogIn, UserPlus } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

export const Navbar: React.FC = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <header className="sticky top-0 z-40 bg-white/95 backdrop-blur border-b border-[#268653]/15 shadow-xs">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-20 flex items-center justify-between">
        {/* Logo */}
        <Link to="/" className="flex items-center gap-3 group">
          <div className="w-11 h-11 rounded-2xl bg-gradient-to-br from-[#174D35] to-[#268653] flex items-center justify-center text-white shadow-md shadow-[#268653]/20 group-hover:scale-105 transition-transform">
            <Sprout className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="text-2xl font-black tracking-tight text-[#174D35]">PestSignal</span>
              <span className="inline-block w-2 h-2 rounded-full bg-[#268653]"></span>
            </div>
            <p className="text-[11px] font-medium text-[#738076] tracking-wide uppercase">AI Agricultural Reporting</p>
          </div>
        </Link>

        {/* Navigation Links */}
        <nav className="hidden md:flex items-center gap-8 text-sm font-medium text-[#18251D]">
          <Link to="/" className="hover:text-[#268653] transition-colors">Home</Link>
          <a href="#how-it-works" className="hover:text-[#268653] transition-colors">How It Works</a>
          <a href="#features" className="hover:text-[#268653] transition-colors">Features</a>
          <a href="#bodhan-ai" className="hover:text-[#268653] transition-colors flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            Bodhan AI Stack
          </a>
          <a href="#about" className="hover:text-[#268653] transition-colors">About</a>
        </nav>

        {/* Actions */}
        <div className="flex items-center gap-3">
          {user ? (
            <div className="flex items-center gap-3">
              <Link
                to={user.role === 'farmer' ? '/farmer/dashboard' : '/admin/dashboard'}
                className="px-4 py-2.5 rounded-xl bg-[#EAF5EC] text-[#174D35] hover:bg-[#268653] hover:text-white font-semibold text-sm transition-all"
              >
                Go to Dashboard ({user.role})
              </Link>
              <button
                onClick={() => {
                  logout();
                  navigate('/');
                }}
                className="px-3.5 py-2 text-sm text-[#738076] hover:text-[#18251D]"
              >
                Logout
              </button>
            </div>
          ) : (
            <>
              <Link
                to="/login"
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl text-sm font-semibold text-[#174D35] hover:bg-[#EAF5EC] transition-all"
              >
                <LogIn className="w-4 h-4" />
                Login
              </Link>
              <Link
                to="/register"
                className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-[#174D35] to-[#268653] hover:shadow-lg hover:shadow-[#268653]/25 hover:-translate-y-0.5 transition-all"
              >
                <UserPlus className="w-4 h-4" />
                Get Started
              </Link>
            </>
          )}
        </div>
      </div>
    </header>
  );
};
