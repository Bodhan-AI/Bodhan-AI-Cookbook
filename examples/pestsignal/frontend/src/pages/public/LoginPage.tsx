import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Sprout, LogIn, KeyRound, Mail, ArrowRight, ShieldCheck, Sparkles, UserCheck } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

export const LoginPage: React.FC = () => {
  const { login } = useAuth();
  const { showToast } = useToast();
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      showToast('warning', 'Missing Fields', 'Please enter your email and password.');
      return;
    }

    setIsLoading(true);
    try {
      const user = await login({ email, password });
      showToast('success', 'Welcome Back', `Logged in as ${user.full_name} (${user.role})`);
      if (user.role === 'farmer') {
        navigate('/farmer/dashboard');
      } else {
        navigate('/admin/dashboard');
      }
    } catch (err: any) {
      console.error('Login error', err);
      showToast('error', 'Login Failed', err.message || 'Invalid email or password.');
    } finally {
      setIsLoading(false);
    }
  };

  // Quick fill helper for internship evaluation demo
  const quickFill = (demoEmail: string, demoPass: string) => {
    setEmail(demoEmail);
    setPassword(demoPass);
  };

  return (
    <div className="min-h-screen bg-[#F7F9F6] flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md text-center">
        <Link to="/" className="inline-flex items-center gap-3">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-[#174D35] to-[#268653] flex items-center justify-center text-white shadow-md">
            <Sprout className="w-6 h-6" />
          </div>
          <span className="text-2xl font-black text-[#174D35] tracking-tight">PestSignal</span>
        </Link>
        <h2 className="mt-4 text-2xl font-black text-[#18251D] tracking-tight">Sign in to your account</h2>
        <p className="mt-1 text-sm text-[#738076]">
          Or{' '}
          <Link to="/register" className="font-semibold text-[#268653] hover:underline">
            create a new account
          </Link>
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md px-4 sm:px-0">
        <div className="bg-white py-8 px-6 shadow-xl rounded-3xl sm:px-10 border border-gray-200/80">
          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider mb-1.5">
                Email Address
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-gray-400">
                  <Mail className="w-4 h-4" />
                </div>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="farmer@pestsignal.org"
                  className="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-xl text-sm placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#268653] focus:border-transparent"
                />
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="block text-xs font-bold text-[#18251D] uppercase tracking-wider">
                  Password
                </label>
                <Link to="/forgot-password" className="text-xs font-medium text-[#268653] hover:underline">
                  Forgot password?
                </Link>
              </div>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-gray-400">
                  <KeyRound className="w-4 h-4" />
                </div>
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="block w-full pl-10 pr-3 py-3 border border-gray-300 rounded-xl text-sm placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#268653] focus:border-transparent"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full flex items-center justify-center gap-2 py-3.5 px-4 rounded-xl text-sm font-bold text-white bg-gradient-to-r from-[#174D35] to-[#268653] hover:shadow-lg hover:shadow-[#268653]/25 active:scale-[0.99] transition-all cursor-pointer disabled:opacity-50"
            >
              {isLoading ? (
                <span>Signing in...</span>
              ) : (
                <>
                  <LogIn className="w-4 h-4" />
                  <span>Sign In</span>
                </>
              )}
            </button>
          </form>

          {/* Internship Demo Quick Fill Shortcuts */}
          <div className="mt-8 pt-6 border-t border-gray-200">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles className="w-4 h-4 text-[#268653]" />
              <span className="text-xs font-bold text-[#174D35] uppercase tracking-wider">
                Instant Demo Logins
              </span>
            </div>
            <div className="grid grid-cols-1 gap-2 text-xs">
              <button
                type="button"
                onClick={() => quickFill('farmer@pestsignal.org', 'farmer123')}
                className="w-full text-left p-2.5 rounded-xl bg-[#EAF5EC] hover:bg-emerald-100 text-[#174D35] font-medium flex items-center justify-between transition-colors"
              >
                <span>👨‍🌾 <strong>Farmer:</strong> Ramu Chowdary</span>
                <span className="text-[10px] bg-white px-2 py-0.5 rounded-md font-bold">Autofill</span>
              </button>
              <button
                type="button"
                onClick={() => quickFill('officer@pestsignal.org', 'officer123')}
                className="w-full text-left p-2.5 rounded-xl bg-blue-50 hover:bg-blue-100 text-blue-900 font-medium flex items-center justify-between transition-colors"
              >
                <span>🔬 <strong>Officer:</strong> Dr. Anita Sharma</span>
                <span className="text-[10px] bg-white px-2 py-0.5 rounded-md font-bold">Autofill</span>
              </button>
              <button
                type="button"
                onClick={() => quickFill('admin@pestsignal.org', 'admin123')}
                className="w-full text-left p-2.5 rounded-xl bg-purple-50 hover:bg-purple-100 text-purple-900 font-medium flex items-center justify-between transition-colors"
              >
                <span>⚡ <strong>Administrator:</strong> Platform Admin</span>
                <span className="text-[10px] bg-white px-2 py-0.5 rounded-md font-bold">Autofill</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
