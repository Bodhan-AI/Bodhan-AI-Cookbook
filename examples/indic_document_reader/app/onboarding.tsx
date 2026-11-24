// ─────────────────────────────────────────────────────────────
// Onboarding Screen — First launch experience
// User selects their preferred language and enters API key
// ─────────────────────────────────────────────────────────────

import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  SafeAreaView,
  KeyboardAvoidingView,
  Platform,
  Alert,
} from 'react-native';
import { router } from 'expo-router';
import { usePreferences } from '../src/contexts/PreferencesContext';
import LanguagePicker from '../src/components/LanguagePicker';
import { setApiKey } from '../src/api/bodhan';
import { getLanguageName, getNativeName } from '../src/constants/languages';
import { Colors, Spacing, FontSize, BorderRadius } from '../src/constants/theme';

type Step = 'language' | 'apikey';

export default function OnboardingScreen() {
  const { completeOnboarding } = usePreferences();
  const [step, setStep] = useState<Step>('language');
  const [selectedLang, setSelectedLang] = useState('');
  const [apiKeyInput, setApiKeyInput] = useState('');

  const handleNext = () => {
    if (step === 'language') {
      if (!selectedLang) {
        Alert.alert('Select a language', 'Please choose your preferred language to continue.');
        return;
      }
      setStep('apikey');
    }
  };

  const handleComplete = async () => {
    if (!apiKeyInput.trim()) {
      Alert.alert('API Key Required', 'Please enter your Bodhan API key. Get one from console.bodhan.ai');
      return;
    }

    setApiKey(apiKeyInput.trim());
    await completeOnboarding(selectedLang, apiKeyInput.trim());
    router.replace('/(tabs)/camera');
  };

  return (
    <SafeAreaView style={styles.container}>
      <KeyboardAvoidingView
        style={styles.inner}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.logo}>📖 Bodhan Reader</Text>
          {step === 'language' ? (
            <>
              <Text style={styles.title}>What language do you think in?</Text>
              <Text style={styles.subtitle}>
                Everything you scan, hear, and read will be translated into this language.
                You can change it anytime.
              </Text>
            </>
          ) : (
            <>
              <Text style={styles.title}>Connect to Bodhan AI</Text>
              <Text style={styles.subtitle}>
                Enter your API key to enable translation, speech, and OCR.
                Get one free at console.bodhan.ai
              </Text>
            </>
          )}
        </View>

        {/* Content */}
        <View style={styles.content}>
          {step === 'language' ? (
            <LanguagePicker
              selected={selectedLang}
              onSelect={setSelectedLang}
            />
          ) : (
            <View style={styles.apiKeySection}>
              <View style={styles.selectedLanguageCard}>
                <Text style={styles.selectedLabel}>Your language</Text>
                <Text style={styles.selectedNative}>{getNativeName(selectedLang)}</Text>
                <Text style={styles.selectedName}>{getLanguageName(selectedLang)}</Text>
                <TouchableOpacity onPress={() => setStep('language')}>
                  <Text style={styles.changeLink}>Change</Text>
                </TouchableOpacity>
              </View>

              <TextInput
                style={styles.apiKeyInput}
                placeholder="Paste your Bodhan API key here"
                placeholderTextColor={Colors.textMuted}
                value={apiKeyInput}
                onChangeText={setApiKeyInput}
                autoCapitalize="none"
                autoCorrect={false}
                secureTextEntry
                multiline={false}
              />

              <Text style={styles.apiKeyHint}>
                🔒 Your key is stored only on this device.
              </Text>
            </View>
          )}
        </View>

        {/* Bottom button */}
        <View style={styles.footer}>
          {step === 'language' ? (
            <TouchableOpacity
              style={[styles.nextButton, !selectedLang && styles.buttonDisabled]}
              onPress={handleNext}
              disabled={!selectedLang}
              activeOpacity={0.8}
            >
              <Text style={styles.nextButtonText}>
                Continue with {selectedLang ? getLanguageName(selectedLang) : '...'} →
              </Text>
            </TouchableOpacity>
          ) : (
            <TouchableOpacity
              style={[styles.nextButton, !apiKeyInput.trim() && styles.buttonDisabled]}
              onPress={handleComplete}
              disabled={!apiKeyInput.trim()}
              activeOpacity={0.8}
            >
              <Text style={styles.nextButtonText}>Start Reading →</Text>
            </TouchableOpacity>
          )}
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  inner: {
    flex: 1,
    padding: Spacing.lg,
  },
  header: {
    marginBottom: Spacing.lg,
  },
  logo: {
    fontSize: FontSize.xxl,
    fontWeight: '800',
    color: Colors.textPrimary,
    marginBottom: Spacing.md,
  },
  title: {
    fontSize: FontSize.xl,
    fontWeight: '700',
    color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
  subtitle: {
    fontSize: FontSize.md,
    color: Colors.textSecondary,
    lineHeight: 22,
  },
  content: {
    flex: 1,
  },
  footer: {
    paddingTop: Spacing.md,
  },
  nextButton: {
    backgroundColor: Colors.primary,
    paddingVertical: Spacing.md,
    borderRadius: BorderRadius.lg,
    alignItems: 'center',
  },
  buttonDisabled: {
    opacity: 0.4,
  },
  nextButtonText: {
    color: Colors.textOnPrimary,
    fontSize: FontSize.lg,
    fontWeight: '700',
  },
  apiKeySection: {
    gap: Spacing.lg,
  },
  selectedLanguageCard: {
    backgroundColor: Colors.surface,
    borderRadius: BorderRadius.md,
    padding: Spacing.lg,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: Colors.primary,
  },
  selectedLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: Spacing.xs,
  },
  selectedNative: {
    fontSize: FontSize.title,
    fontWeight: '800',
    color: Colors.textPrimary,
  },
  selectedName: {
    fontSize: FontSize.base,
    color: Colors.textSecondary,
    marginBottom: Spacing.sm,
  },
  changeLink: {
    color: Colors.accent,
    fontSize: FontSize.md,
    fontWeight: '600',
  },
  apiKeyInput: {
    backgroundColor: Colors.surfaceLight,
    color: Colors.textPrimary,
    borderRadius: BorderRadius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.md,
    fontSize: FontSize.base,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  apiKeyHint: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    textAlign: 'center',
  },
});

