// ─────────────────────────────────────────────────────────────
// User Preferences Context
// Stores preferred language, voice, script, style — set once
// at onboarding, used as defaults across the entire app.
// ─────────────────────────────────────────────────────────────

import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { ScriptMode, TTSStyle } from '../constants/languages';
import { getDefaultVoice } from '../constants/voices';

const STORAGE_KEY = 'bodhan_reader_preferences';

export interface UserPreferences {
  /** Has the user completed onboarding? */
  onboardingComplete: boolean;
  /** Preferred output language code (e.g., 'kn', 'hi', 'te') */
  preferredLanguage: string;
  /** Script mode: native, roman, or codemix */
  scriptMode: ScriptMode;
  /** Preferred TTS voice name */
  voiceName: string;
  /** Preferred TTS speaking style */
  ttsStyle: TTSStyle | 'neutral';
  /** Auto-read aloud after scan/translation */
  autoRead: boolean;
  /** Bodhan API key */
  apiKey: string;
  /** Table format for OCR */
  tableFormat: 'html' | 'markdown';
}

const DEFAULT_PREFERENCES: UserPreferences = {
  onboardingComplete: false,
  preferredLanguage: 'en',
  scriptMode: 'native',
  voiceName: 'Kavya',
  ttsStyle: 'neutral',
  autoRead: true,
  apiKey: '',
  tableFormat: 'html',
};

interface PreferencesContextType {
  preferences: UserPreferences;
  isLoading: boolean;
  updatePreferences: (updates: Partial<UserPreferences>) => Promise<void>;
  completeOnboarding: (language: string, apiKey: string) => Promise<void>;
  resetPreferences: () => Promise<void>;
}

const PreferencesContext = createContext<PreferencesContextType | undefined>(undefined);

export function PreferencesProvider({ children }: { children: React.ReactNode }) {
  const [preferences, setPreferences] = useState<UserPreferences>(DEFAULT_PREFERENCES);
  const [isLoading, setIsLoading] = useState(true);

  // Load preferences from storage on mount
  useEffect(() => {
    (async () => {
      try {
        const stored = await AsyncStorage.getItem(STORAGE_KEY);
        if (stored) {
          const parsed = JSON.parse(stored) as Partial<UserPreferences>;
          setPreferences({ ...DEFAULT_PREFERENCES, ...parsed });
        }
      } catch (error) {
        console.error('Failed to load preferences:', error);
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  const updatePreferences = useCallback(async (updates: Partial<UserPreferences>) => {
    setPreferences(prev => {
      const next = { ...prev, ...updates };
      AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(next)).catch(console.error);
      return next;
    });
  }, []);

  const completeOnboarding = useCallback(async (language: string, apiKey: string) => {
    const defaultVoice = getDefaultVoice(language);
    const updates: Partial<UserPreferences> = {
      onboardingComplete: true,
      preferredLanguage: language,
      voiceName: defaultVoice.name,
      apiKey,
    };
    await updatePreferences(updates);
  }, [updatePreferences]);

  const resetPreferences = useCallback(async () => {
    setPreferences(DEFAULT_PREFERENCES);
    await AsyncStorage.removeItem(STORAGE_KEY);
  }, []);

  return (
    <PreferencesContext.Provider
      value={{ preferences, isLoading, updatePreferences, completeOnboarding, resetPreferences }}
    >
      {children}
    </PreferencesContext.Provider>
  );
}

export function usePreferences(): PreferencesContextType {
  const context = useContext(PreferencesContext);
  if (!context) {
    throw new Error('usePreferences must be used within a PreferencesProvider');
  }
  return context;
}

