// ─────────────────────────────────────────────────────────────
// Quick Translate Screen — Type → Translate → Transliterate → TTS
// Simple text-in, translation-out with script options
// ─────────────────────────────────────────────────────────────

import React, { useState, useCallback, useRef } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  SafeAreaView,
  KeyboardAvoidingView,
  Platform,
  Clipboard,
  Share,
  ActivityIndicator,
} from 'react-native';
import { usePreferences } from '../../src/contexts/PreferencesContext';
import { translate, transliterate } from '../../src/api/bodhan';
import ScriptToggle from '../../src/components/ScriptToggle';
import PlayButton from '../../src/components/PlayButton';
import StatusBanner from '../../src/components/StatusBanner';
import { getLanguageName, getNativeName, TRANSLATION_LANGUAGES } from '../../src/constants/languages';
import type { ScriptMode } from '../../src/constants/languages';
import { Colors, Spacing, FontSize, BorderRadius } from '../../src/constants/theme';

interface TranslationResult {
  id: number;
  inputText: string;
  translatedText: string;
  transliteratedText?: string;
  targetLanguage: string;
  script: ScriptMode;
  truncated: boolean;
}

export default function TranslateScreen() {
  const { preferences } = usePreferences();
  const [inputText, setInputText] = useState('');
  const [scriptMode, setScriptMode] = useState<ScriptMode>(preferences.scriptMode);
  const [isTranslating, setIsTranslating] = useState(false);
  const [results, setResults] = useState<TranslationResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<TextInput>(null);

  const handleTranslate = useCallback(async () => {
    const text = inputText.trim();
    if (!text) return;

    setIsTranslating(true);
    setError(null);

    try {
      // Translate
      const translateResult = await translate({
        text,
        targetLanguage: preferences.preferredLanguage,
        script: scriptMode,
      });

      // Also get transliteration (roman) if script is native and lang isn't en
      let transliteratedText: string | undefined;
      if (
        scriptMode === 'native' &&
        preferences.preferredLanguage !== 'en'
      ) {
        try {
          const transResult = await transliterate({
            text: translateResult.translation,
            language: preferences.preferredLanguage,
            script: 'roman',
          });
          transliteratedText = transResult.transliteration;
        } catch {
          // Transliteration is optional, don't fail the whole thing
        }
      }

      const result: TranslationResult = {
        id: Date.now(),
        inputText: text,
        translatedText: translateResult.translation,
        transliteratedText,
        targetLanguage: preferences.preferredLanguage,
        script: scriptMode,
        truncated: translateResult.truncated,
      };

      setResults(prev => [result, ...prev]);
      setInputText('');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsTranslating(false);
    }
  }, [inputText, preferences.preferredLanguage, scriptMode]);

  const handleCopy = (text: string) => {
    Clipboard.setString(text);
  };

  const handleShare = async (text: string) => {
    try {
      await Share.share({ message: text });
    } catch {}
  };

  return (
    <SafeAreaView style={styles.container}>
      <KeyboardAvoidingView
        style={styles.inner}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={90}
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>✏️ Quick Translate</Text>
          <Text style={styles.subtitle}>
            → {getNativeName(preferences.preferredLanguage)}
          </Text>
        </View>

        {/* Script toggle */}
        <View style={styles.scriptRow}>
          <ScriptToggle
            value={scriptMode}
            onChange={setScriptMode}
            disableCodemix={preferences.preferredLanguage === 'en'}
            disableRoman={preferences.preferredLanguage === 'en'}
          />
        </View>

        {/* Error banner */}
        {error && (
          <View style={styles.errorRow}>
            <StatusBanner
              type="error"
              message="Translation failed"
              detail={error}
              onDismiss={() => setError(null)}
            />
          </View>
        )}

        {/* Results */}
        <ScrollView
          style={styles.resultsList}
          contentContainerStyle={styles.resultsContent}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
        >
          {results.length === 0 && (
            <View style={styles.emptyState}>
              <Text style={styles.emptyIcon}>🌐</Text>
              <Text style={styles.emptyTitle}>Type anything</Text>
              <Text style={styles.emptyText}>
                Enter text in any language and get it translated
                into {getLanguageName(preferences.preferredLanguage)}.
              </Text>
            </View>
          )}

          {results.map(result => (
            <View key={result.id} style={styles.resultCard}>
              {/* Input text */}
              <Text style={styles.inputLabel}>Original</Text>
              <Text style={styles.inputTextDisplay} selectable>
                {result.inputText}
              </Text>

              {/* Translated text */}
              <View style={styles.translationSection}>
                <View style={styles.translationHeader}>
                  <Text style={styles.translationLabel}>
                    {getLanguageName(result.targetLanguage)}
                  </Text>
                  <PlayButton
                    text={result.translatedText}
                    language={result.targetLanguage}
                    size="small"
                  />
                </View>
                <Text style={styles.translatedText} selectable>
                  {result.translatedText}
                </Text>

                {/* Transliteration (if available) */}
                {result.transliteratedText && (
                  <Text style={styles.transliteratedText} selectable>
                    {result.transliteratedText}
                  </Text>
                )}
              </View>

              {/* Actions */}
              <View style={styles.actionRow}>
                <TouchableOpacity
                  style={styles.actionButton}
                  onPress={() => handleCopy(result.translatedText)}
                >
                  <Text style={styles.actionButtonText}>📋 Copy</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.actionButton}
                  onPress={() => handleShare(result.translatedText)}
                >
                  <Text style={styles.actionButtonText}>📤 Share</Text>
                </TouchableOpacity>
              </View>

              {result.truncated && (
                <Text style={styles.truncatedWarning}>⚠ Translation may be incomplete</Text>
              )}
            </View>
          ))}
        </ScrollView>

        {/* Input bar */}
        <View style={styles.inputBar}>
          <TextInput
            ref={inputRef}
            style={styles.textInput}
            placeholder="Type or paste text..."
            placeholderTextColor={Colors.textMuted}
            value={inputText}
            onChangeText={setInputText}
            multiline
            maxLength={5000}
            returnKeyType="default"
          />
          <TouchableOpacity
            style={[
              styles.translateButton,
              (!inputText.trim() || isTranslating) && styles.translateButtonDisabled,
            ]}
            onPress={handleTranslate}
            disabled={!inputText.trim() || isTranslating}
            activeOpacity={0.7}
          >
            {isTranslating ? (
              <ActivityIndicator size="small" color={Colors.textOnPrimary} />
            ) : (
              <Text style={styles.translateButtonText}>→</Text>
            )}
          </TouchableOpacity>
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
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.md,
  },
  title: {
    fontSize: FontSize.xl,
    fontWeight: '700',
    color: Colors.textPrimary,
  },
  subtitle: {
    fontSize: FontSize.lg,
    color: Colors.accent,
    fontWeight: '700',
  },
  scriptRow: {
    paddingHorizontal: Spacing.lg,
    marginBottom: Spacing.sm,
  },
  errorRow: {
    paddingHorizontal: Spacing.lg,
  },
  resultsList: {
    flex: 1,
  },
  resultsContent: {
    padding: Spacing.md,
    paddingBottom: Spacing.xxl,
  },
  emptyState: {
    alignItems: 'center',
    paddingVertical: Spacing.xxl * 2,
    paddingHorizontal: Spacing.xl,
  },
  emptyIcon: {
    fontSize: 64,
    marginBottom: Spacing.lg,
  },
  emptyTitle: {
    fontSize: FontSize.xl,
    fontWeight: '700',
    color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
  emptyText: {
    fontSize: FontSize.md,
    color: Colors.textSecondary,
    textAlign: 'center',
    lineHeight: 22,
  },
  resultCard: {
    backgroundColor: Colors.surface,
    borderRadius: BorderRadius.md,
    padding: Spacing.md,
    marginBottom: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  inputLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: 4,
  },
  inputTextDisplay: {
    fontSize: FontSize.md,
    color: Colors.textSecondary,
    lineHeight: 22,
    marginBottom: Spacing.md,
  },
  translationSection: {
    backgroundColor: Colors.surfaceLight,
    borderRadius: BorderRadius.sm,
    padding: Spacing.md,
  },
  translationHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: Spacing.sm,
  },
  translationLabel: {
    fontSize: FontSize.xs,
    color: Colors.accent,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  translatedText: {
    fontSize: FontSize.lg,
    color: Colors.textPrimary,
    lineHeight: 28,
  },
  transliteratedText: {
    fontSize: FontSize.md,
    color: Colors.textMuted,
    fontStyle: 'italic',
    marginTop: Spacing.sm,
    lineHeight: 22,
  },
  actionRow: {
    flexDirection: 'row',
    gap: Spacing.sm,
    marginTop: Spacing.md,
  },
  actionButton: {
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.md,
    backgroundColor: Colors.surfaceLight,
    borderRadius: BorderRadius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  actionButtonText: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  truncatedWarning: {
    fontSize: FontSize.xs,
    color: Colors.warning,
    marginTop: Spacing.sm,
  },
  inputBar: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    borderTopWidth: 1,
    borderTopColor: Colors.border,
    backgroundColor: Colors.background,
    gap: Spacing.sm,
  },
  textInput: {
    flex: 1,
    backgroundColor: Colors.surface,
    color: Colors.textPrimary,
    borderRadius: BorderRadius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 4,
    fontSize: FontSize.base,
    maxHeight: 120,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  translateButton: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: Colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  translateButtonDisabled: {
    opacity: 0.4,
  },
  translateButtonText: {
    fontSize: FontSize.xl,
    color: Colors.textOnPrimary,
    fontWeight: '700',
  },
});

