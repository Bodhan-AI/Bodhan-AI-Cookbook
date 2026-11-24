// ─────────────────────────────────────────────────────────────
// Live Listener Screen — Record → STT → Translate → Read
// Real-time speech translation with subtitle display
// ─────────────────────────────────────────────────────────────

import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  SafeAreaView,
} from 'react-native';
import { usePreferences } from '../../src/contexts/PreferencesContext';
import { useAudioRecorder } from '../../src/hooks/useAudioRecorder';
import { listenAndTranslate, textToSpeech } from '../../src/api/bodhan';
import { useAudioPlayer } from '../../src/hooks/useAudioPlayer';
import ScriptToggle from '../../src/components/ScriptToggle';
import PlayButton from '../../src/components/PlayButton';
import StatusBanner from '../../src/components/StatusBanner';
import { getLanguageName, STT_LANGUAGES } from '../../src/constants/languages';
import type { ScriptMode } from '../../src/constants/languages';
import { Colors, Spacing, FontSize, BorderRadius } from '../../src/constants/theme';

interface TranslationEntry {
  id: number;
  timestamp: Date;
  originalText: string;
  translatedText: string;
  truncated: boolean;
}

export default function ListenerScreen() {
  const { preferences } = usePreferences();
  const recorder = useAudioRecorder();
  const player = useAudioPlayer();

  const [entries, setEntries] = useState<TranslationEntry[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [scriptMode, setScriptMode] = useState<ScriptMode>(preferences.scriptMode);
  const [sourceLanguage, setSourceLanguage] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [showSourcePicker, setShowSourcePicker] = useState(false);

  const handleToggleRecording = useCallback(async () => {
    if (recorder.isRecording) {
      // Stop recording and process
      const audioUri = await recorder.stopRecording();
      if (!audioUri) return;

      setIsProcessing(true);
      setError(null);

      try {
        const result = await listenAndTranslate(
          audioUri,
          preferences.preferredLanguage,
          sourceLanguage,
          scriptMode
        );

        const entry: TranslationEntry = {
          id: Date.now(),
          timestamp: new Date(),
          originalText: result.transcript,
          translatedText: result.translation,
          truncated: result.truncated,
        };

        setEntries(prev => [entry, ...prev]);

        // Auto-read if enabled
        if (preferences.autoRead) {
          try {
            const audioFileUri = await textToSpeech({
              text: result.translation,
              voice: preferences.voiceName,
              language: preferences.preferredLanguage,
              style: preferences.ttsStyle === 'neutral' ? undefined : preferences.ttsStyle,
            });
            await player.play(audioFileUri);
          } catch (ttsErr) {
            console.error('TTS auto-read failed:', ttsErr);
          }
        }
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setIsProcessing(false);
      }
    } else {
      // Start recording
      setError(null);
      await recorder.startRecording();
    }
  }, [recorder, preferences, sourceLanguage, scriptMode, player]);

  const formatDuration = (ms: number) => {
    const seconds = Math.floor(ms / 1000);
    return `${seconds}s / 30s`;
  };

  const formatTime = (date: Date) => {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <SafeAreaView style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>🎙️ Live Listener</Text>
        <Text style={styles.subtitle}>
          → {getLanguageName(preferences.preferredLanguage)}
        </Text>
      </View>

      {/* Source language selector */}
      <TouchableOpacity
        style={styles.sourceSelector}
        onPress={() => setShowSourcePicker(!showSourcePicker)}
      >
        <Text style={styles.sourceSelectorLabel}>Listening for:</Text>
        <Text style={styles.sourceSelectorValue}>
          {sourceLanguage ? getLanguageName(sourceLanguage) : 'Auto-detect'} ▾
        </Text>
      </TouchableOpacity>

      {showSourcePicker && (
        <ScrollView horizontal style={styles.sourcePicker} showsHorizontalScrollIndicator={false}>
          <TouchableOpacity
            style={[styles.sourceChip, !sourceLanguage && styles.sourceChipActive]}
            onPress={() => { setSourceLanguage(undefined); setShowSourcePicker(false); }}
          >
            <Text style={[styles.sourceChipText, !sourceLanguage && styles.sourceChipTextActive]}>
              Auto
            </Text>
          </TouchableOpacity>
          {STT_LANGUAGES.map(lang => (
            <TouchableOpacity
              key={lang.code}
              style={[styles.sourceChip, sourceLanguage === lang.code && styles.sourceChipActive]}
              onPress={() => { setSourceLanguage(lang.code); setShowSourcePicker(false); }}
            >
              <Text style={[styles.sourceChipText, sourceLanguage === lang.code && styles.sourceChipTextActive]}>
                {lang.name}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      )}

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
        <StatusBanner
          type="error"
          message="Translation failed"
          detail={error}
          onDismiss={() => setError(null)}
          onRetry={handleToggleRecording}
        />
      )}

      {/* Processing indicator */}
      {isProcessing && (
        <StatusBanner
          type="loading"
          message="Translating speech..."
        />
      )}

      {/* Translation entries */}
      <ScrollView
        style={styles.entriesList}
        contentContainerStyle={styles.entriesContent}
        showsVerticalScrollIndicator={false}
      >
        {entries.length === 0 && !recorder.isRecording && !isProcessing && (
          <View style={styles.emptyState}>
            <Text style={styles.emptyIcon}>🎧</Text>
            <Text style={styles.emptyTitle}>Ready to listen</Text>
            <Text style={styles.emptyText}>
              Tap the microphone button and speak or point your phone at someone speaking.
              The speech will be translated into {getLanguageName(preferences.preferredLanguage)}.
            </Text>
          </View>
        )}

        {entries.map(entry => (
          <View key={entry.id} style={styles.entryCard}>
            <View style={styles.entryHeader}>
              <Text style={styles.entryTime}>{formatTime(entry.timestamp)}</Text>
              <PlayButton text={entry.translatedText} size="small" />
            </View>

            {/* Translated text (prominent) */}
            <Text style={styles.translatedText} selectable>
              {entry.translatedText}
            </Text>

            {/* Original text (subtle) */}
            <View style={styles.originalSection}>
              <Text style={styles.originalLabel}>Original:</Text>
              <Text style={styles.originalText} selectable>
                {entry.originalText}
              </Text>
            </View>

            {entry.truncated && (
              <Text style={styles.truncatedWarning}>⚠ Translation may be incomplete</Text>
            )}
          </View>
        ))}
      </ScrollView>

      {/* Recording controls */}
      <View style={styles.recordingBar}>
        {recorder.isRecording && (
          <View style={styles.durationBar}>
            <View style={styles.recordingDot} />
            <Text style={styles.durationText}>
              Recording... {formatDuration(recorder.durationMs)}
            </Text>
          </View>
        )}

        <TouchableOpacity
          style={[
            styles.recordButton,
            recorder.isRecording && styles.recordButtonActive,
            isProcessing && styles.recordButtonDisabled,
          ]}
          onPress={handleToggleRecording}
          disabled={isProcessing}
          activeOpacity={0.7}
        >
          <Text style={styles.recordButtonIcon}>
            {recorder.isRecording ? '⏹' : '🎙️'}
          </Text>
          <Text style={styles.recordButtonText}>
            {recorder.isRecording ? 'Stop & Translate' : 'Start Listening'}
          </Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Colors.background,
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
    fontSize: FontSize.md,
    color: Colors.accent,
    fontWeight: '600',
  },
  sourceSelector: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.sm,
    gap: Spacing.sm,
  },
  sourceSelectorLabel: {
    fontSize: FontSize.md,
    color: Colors.textSecondary,
  },
  sourceSelectorValue: {
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    fontWeight: '600',
  },
  sourcePicker: {
    paddingHorizontal: Spacing.lg,
    maxHeight: 44,
    marginBottom: Spacing.sm,
  },
  sourceChip: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    backgroundColor: Colors.surface,
    borderRadius: BorderRadius.full,
    marginRight: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  sourceChipActive: {
    backgroundColor: Colors.primary,
    borderColor: Colors.primary,
  },
  sourceChipText: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  sourceChipTextActive: {
    color: Colors.textOnPrimary,
    fontWeight: '600',
  },
  scriptRow: {
    paddingHorizontal: Spacing.lg,
    marginBottom: Spacing.sm,
  },
  entriesList: {
    flex: 1,
  },
  entriesContent: {
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
  entryCard: {
    backgroundColor: Colors.surface,
    borderRadius: BorderRadius.md,
    padding: Spacing.md,
    marginBottom: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  entryHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: Spacing.sm,
  },
  entryTime: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  translatedText: {
    fontSize: FontSize.lg,
    color: Colors.textPrimary,
    lineHeight: 28,
    marginBottom: Spacing.sm,
  },
  originalSection: {
    paddingTop: Spacing.sm,
    borderTopWidth: 1,
    borderTopColor: Colors.border,
  },
  originalLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: 4,
  },
  originalText: {
    fontSize: FontSize.md,
    color: Colors.textSecondary,
    lineHeight: 22,
  },
  truncatedWarning: {
    fontSize: FontSize.xs,
    color: Colors.warning,
    marginTop: Spacing.sm,
  },
  recordingBar: {
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.md,
    borderTopWidth: 1,
    borderTopColor: Colors.border,
    backgroundColor: Colors.background,
  },
  durationBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: Spacing.sm,
    gap: Spacing.sm,
  },
  recordingDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: Colors.accentRed,
  },
  durationText: {
    fontSize: FontSize.md,
    color: Colors.accentRed,
    fontWeight: '600',
  },
  recordButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Colors.primary,
    paddingVertical: Spacing.md,
    borderRadius: BorderRadius.lg,
    gap: Spacing.sm,
  },
  recordButtonActive: {
    backgroundColor: Colors.accentRed,
  },
  recordButtonDisabled: {
    opacity: 0.5,
  },
  recordButtonIcon: {
    fontSize: 20,
  },
  recordButtonText: {
    color: Colors.textOnPrimary,
    fontSize: FontSize.lg,
    fontWeight: '700',
  },
});

