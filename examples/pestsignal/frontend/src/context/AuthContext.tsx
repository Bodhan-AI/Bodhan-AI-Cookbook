import React, { createContext, useContext, useState, useEffect } from 'react';
import { User } from '../types';
import { authApi } from '../services/api';

interface AuthContextType {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  login: (credentials: any) => Promise<User>;
  register: (userData: any) => Promise<User>;
  logout: () => void;
  refreshUser: () => Promise<void>;
  isFarmer: boolean;
  isOfficer: boolean;
  isAdmin: boolean;
  canAccessAdmin: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(() => {
    const saved = localStorage.getItem('pestsignal_user');
    return saved ? JSON.parse(saved) : null;
  });
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('pestsignal_token'));
  const [isLoading, setIsLoading] = useState<boolean>(true);

  useEffect(() => {
    async function initAuth() {
      if (token) {
        try {
          const freshUser = await authApi.getMe();
          setUser(freshUser);
          localStorage.setItem('pestsignal_user', JSON.stringify(freshUser));
        } catch {
          logout();
        }
      }
      setIsLoading(false);
    }
    initAuth();
  }, [token]);

  const login = async (credentials: any): Promise<User> => {
    const res = await authApi.login(credentials);
    setToken(res.access_token);
    setUser(res.user);
    localStorage.setItem('pestsignal_token', res.access_token);
    localStorage.setItem('pestsignal_user', JSON.stringify(res.user));
    return res.user;
  };

  const register = async (userData: any): Promise<User> => {
    const res = await authApi.register(userData);
    setToken(res.access_token);
    setUser(res.user);
    localStorage.setItem('pestsignal_token', res.access_token);
    localStorage.setItem('pestsignal_user', JSON.stringify(res.user));
    return res.user;
  };

  const logout = () => {
    setUser(null);
    setToken(null);
    localStorage.removeItem('pestsignal_token');
    localStorage.removeItem('pestsignal_user');
  };

  const refreshUser = async () => {
    if (token) {
      try {
        const freshUser = await authApi.getMe();
        setUser(freshUser);
        localStorage.setItem('pestsignal_user', JSON.stringify(freshUser));
      } catch (e) {
        console.error('Failed to refresh user', e);
      }
    }
  };

  const isFarmer = user?.role === 'farmer';
  const isOfficer = user?.role === 'officer';
  const isAdmin = user?.role === 'admin';
  const canAccessAdmin = isOfficer || isAdmin;

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isLoading,
        login,
        register,
        logout,
        refreshUser,
        isFarmer,
        isOfficer,
        isAdmin,
        canAccessAdmin,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
