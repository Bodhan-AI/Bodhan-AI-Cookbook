// ─────────────────────────────────────────────────────────────
// Settings Screen — Manage preferences, voice, style, API key
// ─────────────────────────────────────────────────────────────

import React, { useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  ScrollView,
  Switch,
  TextInput,
  StyleSheet,
  SafeAreaView,
  Alert,
} from 'react-native';
import { usePreferences } from '../../src/contexts/PreferencesContext';
import { setApiKey } from '../../src/api/bodhan';
import LanguagePicker from '../../src/components/LanguagePicker';
import VoicePicker from '../../src/components/VoicePicker';
import ScriptToggle from '../../src/components/ScriptToggle';
import { getLanguageName, getNativeName, TTS_STYLES } from '../../src/constants/languages';
import type { ScriptMode, TTSStyle } from '../../src/constants/languages';
import { Colors, Spacing, FontSize, BorderRadius } from '../../src/constants/theme';

type SettingsSection = 'main' | 'language' | 'voice' | 'style';

export default function SettingsScreen() {
  const { preferences, updatePreferences, resetPreferences } = usePreferences();
  const [activeSection, setActiveSection] = useState<SettingsSection>('main');
  const [apiKeyInput, setApiKeyInput] = useState(preferences.apiKey);
  const [showApiKey, setShowApiKey] = useState(false);

  const handleLanguageChange = async (code: string) => {
    await updatePreferences({ preferredLanguage: code });
    setActiveSection('main');
  };

  const handleVoiceChange = async (voiceName: string) => {
    await updatePreferences({ voiceName });
    setActiveSection('main');
  };

  const handleStyleChange = async (style: TTSStyle | 'neutral') => {
    await updatePreferences({ ttsStyle: style });
  };

  const handleScriptChange = async (mode: ScriptMode) => {
    await updatePreferences({ scriptMode: mode });
  };

  const handleApiKeySave = async () => {
    const key = apiKeyInput.trim();
    if (!key) {
      Alert.alert('Error', 'API key cannot be empty');
      return;
    }
    setApiKey(key);
    await updatePreferences({ apiKey: key });
    Alert.alert('Saved', 'API key updated successfully');
  };

  const handleReset = () => {
    Alert.alert(
      'Reset All Settings',
      'This will clear all preferences and return to onboarding. Are you sure?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Reset',
          style: 'destructive',
          onPress: async () => {
            await resetPreferences();
          },
        },
      ]
    );
  };

  // ── Sub-sections ──

  if (activeSection === 'language') {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.subHeader}>
          <TouchableOpacity onPress={() => setActiveSection('main')}>
            <Text style={styles.backText}>← Back</Text>
          </TouchableOpacity>
          <Text style={styles.subTitle}>Preferred Language</Text>
          <View style={{ width: 60 }} />
        </View>
        <View style={styles.pickerContainer}>
          <LanguagePicker
            selected={preferences.preferredLanguage}
            onSelect={handleLanguageChange}
          />
        </View>
      </SafeAreaView>
    );
  }

  if (activeSection === 'voice') {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.subHeader}>
          <TouchableOpacity onPress={() => setActiveSection('main')}>
            <Text style={styles.backText}>← Back</Text>
          </TouchableOpacity>
          <Text style={styles.subTitle}>Voice</Text>
          <View style={{ width: 60 }} />
        </View>
        <View style={styles.pickerContainer}>
          <VoicePicker
            selected={preferences.voiceName}
            onSelect={handleVoiceChange}
          />
        </View>
      </SafeAreaView>
    );
  }

  // ── Main settings ──

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        <Text style={styles.title}>⚙️ Settings</Text>

        {/* Language */}
        <Text style={styles.sectionLabel}>LANGUAGE & SCRIPT</Text>
        <TouchableOpacity
          style={styles.settingRow}
          onPress={() => setActiveSection('language')}
        >
          <View>
            <Text style={styles.settingLabel}>Preferred Language</Text>
            <Text style={styles.settingValue}>
              {getNativeName(preferences.preferredLanguage)} — {getLanguageName(preferences.preferredLanguage)}
            </Text>
          </View>
          <Text style={styles.chevron}>›</Text>
        </TouchableOpacity>

        <View style={styles.settingRowStatic}>
          <Text style={styles.settingLabel}>Default Script</Text>
          <View style={styles.scriptToggleContainer}>
            <ScriptToggle
              value={preferences.scriptMode}
              onChange={handleScriptChange}
              disableCodemix={preferences.preferredLanguage === 'en'}
              disableRoman={preferences.preferredLanguage === 'en'}
            />
          </View>
        </View>

        {/* Voice & Style */}
        <Text style={styles.sectionLabel}>VOICE & STYLE</Text>
        <TouchableOpacity
          style={styles.settingRow}
          onPress={() => setActiveSection('voice')}
        >
          <View>
            <Text style={styles.settingLabel}>Voice</Text>
            <Text style={styles.settingValue}>{preferences.voiceName}</Text>
          </View>
          <Text style={styles.chevron}>›</Text>
        </TouchableOpacity>

        <View style={styles.settingRowStatic}>
          <Text style={styles.settingLabel}>Reading Style</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.styleScroller}>
            {TTS_STYLES.map(s => (
              <TouchableOpacity
                key={s.value}
                style={[
                  styles.styleChip,
                  preferences.ttsStyle === s.value && styles.styleChipActive,
                ]}
                onPress={() => handleStyleChange(s.value)}
              >
                <Text style={styles.styleChipIcon}>{s.icon}</Text>
                <Text
                  style={[
                    styles.styleChipText,
                    preferences.ttsStyle === s.value && styles.styleChipTextActive,
                  ]}
                >
                  {s.label}
                </Text>
              </TouchableOpacity>
            ))}
          </ScrollView>
        </View>

        {/* Behavior */}
        <Text style={styles.sectionLabel}>BEHAVIOR</Text>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>Auto-read after scan</Text>
          <Switch
            value={preferences.autoRead}
            onValueChange={v => updatePreferences({ autoRead: v })}
            trackColor={{ false: Colors.border, true: Colors.primaryLight }}
            thumbColor={preferences.autoRead ? Colors.primary : Colors.textMuted}
          />
        </View>

        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>Table format (OCR)</Text>
          <View style={styles.tableFormatRow}>
            {(['html', 'markdown'] as const).map(fmt => (
              <TouchableOpacity
                key={fmt}
                style={[
                  styles.tableFormatChip,
                  preferences.tableFormat === fmt && styles.tableFormatChipActive,
                ]}
                onPress={() => updatePreferences({ tableFormat: fmt })}
              >
                <Text
                  style={[
                    styles.tableFormatText,
                    preferences.tableFormat === fmt && styles.tableFormatTextActive,
                  ]}
                >
                  {fmt.toUpperCase()}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* API Key */}
        <Text style={styles.sectionLabel}>API KEY</Text>
        <View style={styles.apiKeySection}>
          <View style={styles.apiKeyInputRow}>
            <TextInput
              style={styles.apiKeyInput}
              value={apiKeyInput}
              onChangeText={setApiKeyInput}
              secureTextEntry={!showApiKey}
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="Your Bodhan API key"
              placeholderTextColor={Colors.textMuted}
            />
            <TouchableOpacity onPress={() => setShowApiKey(!showApiKey)}>
              <Text style={styles.showHideText}>{showApiKey ? '🙈' : '👁️'}</Text>
            </TouchableOpacity>
          </View>
          <TouchableOpacity style={styles.saveKeyButton} onPress={handleApiKeySave}>
            <Text style={styles.saveKeyText}>Save Key</Text>
          </TouchableOpacity>
        </View>

        {/* Danger zone */}
        <Text style={styles.sectionLabel}>DANGER ZONE</Text>
        <TouchableOpacity style={styles.resetButton} onPress={handleReset}>
          <Text style={styles.resetButtonText}>Reset All Settings</Text>
        </TouchableOpacity>

        {/* Footer */}
        <View style={styles.footer}>
          <Text style={styles.footerText}>Bodhan Reader v1.0.0</Text>
          <Text style={styles.footerText}>Powered by Bodhan AI APIs</Text>
          <Text style={styles.footerLink}>console.bodhan.ai</Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  content: {
    padding: Spacing.lg,
    paddingBottom: Spacing.xxl * 2,
  },
  title: {
    fontSize: FontSize.xxl,
    fontWeight: '800',
    color: Colors.textPrimary,
    marginBottom: Spacing.lg,
  },
  sectionLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: '700',
    letterSpacing: 1.5,
    marginTop: Spacing.lg,
    marginBottom: Spacing.sm,
  },
  settingRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: Colors.surface,
    padding: Spacing.md,
    borderRadius: BorderRadius.md,
    marginBottom: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  settingRowStatic: {
    backgroundColor: Colors.surface,
    padding: Spacing.md,
    borderRadius: BorderRadius.md,
    marginBottom: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  settingLabel: {
    fontSize: FontSize.base,
    color: Colors.textPrimary,
    fontWeight: '500',
  },
  settingValue: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    marginTop: 2,
  },
  chevron: {
    fontSize: FontSize.xl,
    color: Colors.textMuted,
  },
  scriptToggleContainer: {
    marginTop: Spacing.sm,
  },
  styleScroller: {
    marginTop: Spacing.sm,
  },
  styleChip: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    backgroundColor: Colors.surfaceLight,
    borderRadius: BorderRadius.full,
    marginRight: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    gap: 4,
  },
  styleChipActive: {
    backgroundColor: Colors.primary,
    borderColor: Colors.primary,
  },
  styleChipIcon: {
    fontSize: 14,
  },
  styleChipText: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  styleChipTextActive: {
    color: Colors.textOnPrimary,
    fontWeight: '600',
  },
  tableFormatRow: {
    flexDirection: 'row',
    gap: Spacing.sm,
  },
  tableFormatChip: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    backgroundColor: Colors.surfaceLight,
    borderRadius: BorderRadius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  tableFormatChipActive: {
    backgroundColor: Colors.primary,
    borderColor: Colors.primary,
  },
  tableFormatText: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontWeight: '600',
  },
  tableFormatTextActive: {
    color: Colors.textOnPrimary,
  },
  apiKeySection: {
    backgroundColor: Colors.surface,
    padding: Spacing.md,
    borderRadius: BorderRadius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    gap: Spacing.sm,
  },
  apiKeyInputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.sm,
  },
  apiKeyInput: {
    flex: 1,
    backgroundColor: Colors.surfaceLight,
    color: Colors.textPrimary,
    borderRadius: BorderRadius.sm,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    fontSize: FontSize.md,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  showHideText: {
    fontSize: 20,
    padding: 4,
  },
  saveKeyButton: {
    backgroundColor: Colors.primary,
    paddingVertical: Spacing.sm,
    borderRadius: BorderRadius.sm,
    alignItems: 'center',
  },
  saveKeyText: {
    color: Colors.textOnPrimary,
    fontSize: FontSize.md,
    fontWeight: '600',
  },
  resetButton: {
    backgroundColor: Colors.surface,
    padding: Spacing.md,
    borderRadius: BorderRadius.md,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: Colors.accentRed,
  },
  resetButtonText: {
    color: Colors.accentRed,
    fontSize: FontSize.base,
    fontWeight: '600',
  },
  footer: {
    alignItems: 'center',
    marginTop: Spacing.xxl,
    gap: 4,
  },
  footerText: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
  },
  footerLink: {
    fontSize: FontSize.sm,
    color: Colors.accent,
    marginTop: 4,
  },
  // Sub-pages
  subHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
  },
  subTitle: {
    fontSize: FontSize.lg,
    fontWeight: '700',
    color: Colors.textPrimary,
  },
  backText: {
    color: Colors.accent,
    fontSize: FontSize.md,
    fontWeight: '600',
  },
  pickerContainer: {
    flex: 1,
    padding: Spacing.lg,
  },
});

