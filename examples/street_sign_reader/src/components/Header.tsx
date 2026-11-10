// File: src/components/Header.tsx
import React from 'react';
import { Sun, Moon, Key, SlidersHorizontal } from 'lucide-react';
import { AppSettings } from '../models/SignboardTypes';
import bodhanLogo from '../assets/bodhan-logo.png';

interface HeaderProps {
  settings: AppSettings;
  onToggleTheme: () => void;
  onOpenSettings: () => void;
  activeTab: string;
}

export const Header: React.FC<HeaderProps> = ({
  settings,
  onToggleTheme,
  onOpenSettings,
  activeTab
}) => {
  const hasKey = Boolean(
    (settings.bodhanApiKey && settings.bodhanApiKey.length > 5) ||
      (settings.bodhanOcrKey && settings.bodhanOcrKey.length > 5)
  );
  const currentTheme =
    settings.theme === 'system'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
      : settings.theme;

  return (
    <header className="bodhan-header">
      <div className="brand-badge-row">
        <div
          className="brand-flame"
          style={{
            background: '#fffdf9',
            border: '1.5px solid rgba(255, 85, 0, 0.4)',
            borderRadius: 'var(--r-md)',
            overflow: 'hidden',
            padding: '2px',
            boxShadow: '0 2px 8px rgba(255, 85, 0, 0.25)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '34px',
            height: '34px'
          }}
        >
          <img
            src={bodhanLogo}
            alt="Bodhan AI Origami Swan Emblem"
            style={{ width: '100%', height: '100%', objectFit: 'contain' }}
          />
        </div>
        <div>
          <div className="brand-text">
            Bodhan<span>.AI</span>
          </div>
        </div>
        <span className="recipe-tag">BoardVaani</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        {/* BYOK Status Pill */}
        <button
          onClick={onOpenSettings}
          className="btn btn-ghost"
          style={{
            padding: '4px 10px',
            fontSize: '0.78rem',
            border: `1px solid ${hasKey ? 'rgba(15, 122, 78, 0.3)' : 'var(--brand-line)'}`,
            backgroundColor: hasKey ? 'var(--ok-wash)' : 'var(--brand-wash)',
            color: hasKey ? 'var(--ok)' : 'var(--brand)'
          }}
          title={hasKey ? 'Bodhan API Key Active' : 'Enter Bodhan API Key in Settings'}
        >
          <Key size={13} />
          {hasKey ? 'BYOK Active' : 'Demo / Enter Key'}
        </button>

        {/* Theme Toggle (matching console.bodhan.ai) */}
        <button
          onClick={onToggleTheme}
          className="btn btn-ghost btn-icon"
          title={`Switch to ${currentTheme === 'dark' ? 'Light' : 'Dark'} Mode`}
          aria-label="Toggle Theme"
        >
          {currentTheme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
        </button>

        {/* Settings Button */}
        <button
          onClick={onOpenSettings}
          className={`btn btn-icon ${activeTab === 'settings' ? 'btn-brand' : 'btn-ghost'}`}
          title="App Settings & Voice Catalog"
          aria-label="Settings"
        >
          <SlidersHorizontal size={18} />
        </button>
      </div>
    </header>
  );
};
