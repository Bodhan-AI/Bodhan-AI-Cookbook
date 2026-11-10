// File: src/services/secureStorage.ts
import { AppSettings, HistoryScanItem } from '../models/SignboardTypes';

const STORAGE_KEYS = {
  API_KEY: 'bodhan_api_key',
  SETTINGS: 'boardvaani_settings',
  HISTORY: 'boardvaani_scan_history',
  THEME: 'bodhan-theme'
};

const DEFAULT_SETTINGS: AppSettings = {
  bodhanApiKey: '',
  bodhanOcrKey: '',
  bodhanTranslateKey: '',
  bodhanTransliterateKey: '',
  bodhanTtsKey: '',
  useSeparateKeys: false,
  targetLanguage: 'en', // English default
  preferredVoice: 'Arun',
  playbackSpeed: 1.0,
  autoPlayAudio: true,
  enableImageCropper: true,
  offlineDemoMode: false,
  theme: 'system',
  enableGemmaSummarizer: true,
  enableGemmaHindi: true,
  includeExtraContext: true,
  gemmaProvider: 'builtin',
  gemmaApiKey: '',
  gemmaModel: 'llama-3.3-70b-versatile'
};

export function getStoredSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.SETTINGS);
    const parsed = raw ? JSON.parse(raw) : {};
    const key = localStorage.getItem(STORAGE_KEYS.API_KEY) || '';
    const theme = (localStorage.getItem(STORAGE_KEYS.THEME) as AppSettings['theme']) || 'system';

    // Auto-migrate decommissioned gemma2-9b-it to llama-3.3-70b-versatile
    if (parsed.gemmaModel === 'gemma2-9b-it' || parsed.gemmaModel === 'llama-3.1-8b-instant') {
      parsed.gemmaModel = 'llama-3.3-70b-versatile';
    }

    return {
      ...DEFAULT_SETTINGS,
      ...parsed,
      bodhanApiKey: parsed.bodhanApiKey || key,
      theme
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(settings: Partial<AppSettings>): AppSettings {
  const current = getStoredSettings();
  const updated: AppSettings = { ...current, ...settings };

  if (settings.bodhanApiKey !== undefined) {
    localStorage.setItem(STORAGE_KEYS.API_KEY, settings.bodhanApiKey.trim());
  }

  if (settings.theme !== undefined) {
    localStorage.setItem(STORAGE_KEYS.THEME, settings.theme);
    applyTheme(settings.theme);
  }

  localStorage.setItem(STORAGE_KEYS.SETTINGS, JSON.stringify(updated));
  return updated;
}

export function getStoredApiKey(): string {
  return (localStorage.getItem(STORAGE_KEYS.API_KEY) || '').trim();
}

export function getOcrApiKey(): string {
  const settings = getStoredSettings();
  if (settings.bodhanOcrKey?.trim()) {
    return settings.bodhanOcrKey.trim();
  }
  return getStoredApiKey();
}

export function getTranslateApiKey(): string {
  const settings = getStoredSettings();
  if (settings.bodhanTranslateKey?.trim()) {
    return settings.bodhanTranslateKey.trim();
  }
  return getStoredApiKey();
}

export function getTransliterateApiKey(): string {
  const settings = getStoredSettings();
  if (settings.bodhanTransliterateKey?.trim()) {
    return settings.bodhanTransliterateKey.trim();
  }
  return getTranslateApiKey();
}

export function getTtsApiKey(): string {
  const settings = getStoredSettings();
  if (settings.bodhanTtsKey?.trim()) {
    return settings.bodhanTtsKey.trim();
  }
  return getStoredApiKey();
}

export function hasAnyBodhanKey(): boolean {
  const settings = getStoredSettings();
  return Boolean(
    settings.bodhanApiKey?.trim() ||
      settings.bodhanOcrKey?.trim() ||
      settings.bodhanTranslateKey?.trim() ||
      settings.bodhanTtsKey?.trim()
  );
}

export function saveApiKey(key: string): void {
  localStorage.setItem(STORAGE_KEYS.API_KEY, key.trim());
}

export function clearApiKey(): void {
  localStorage.removeItem(STORAGE_KEYS.API_KEY);
}

export function applyTheme(themePref: 'light' | 'dark' | 'system'): void {
  const root = document.documentElement;
  const isDark =
    themePref === 'dark' ||
    (themePref === 'system' &&
      window.matchMedia &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);

  root.setAttribute('data-theme', isDark ? 'dark' : 'light');
  root.setAttribute('data-theme-pref', themePref);
}

// History Management: stores last 10 scans (FIFO eviction)
export function getStoredHistory(): HistoryScanItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.HISTORY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function saveScanToHistory(item: HistoryScanItem): HistoryScanItem[] {
  try {
    const history = getStoredHistory().filter((h) => h.id !== item.id);
    const updated = [item, ...history].slice(0, 10); // Keep max 10
    localStorage.setItem(STORAGE_KEYS.HISTORY, JSON.stringify(updated));
    return updated;
  } catch {
    return [];
  }
}

export function deleteScanFromHistory(id: string): HistoryScanItem[] {
  try {
    const history = getStoredHistory().filter((h) => h.id !== id);
    localStorage.setItem(STORAGE_KEYS.HISTORY, JSON.stringify(history));
    return history;
  } catch {
    return [];
  }
}

export function clearHistory(): void {
  localStorage.removeItem(STORAGE_KEYS.HISTORY);
}
