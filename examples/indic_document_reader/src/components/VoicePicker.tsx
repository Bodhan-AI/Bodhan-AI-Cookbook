// ─────────────────────────────────────────────────────────────
// VoicePicker — Select a TTS voice persona
// ─────────────────────────────────────────────────────────────

import React from 'react';
import { View, Text, TouchableOpacity, FlatList, StyleSheet } from 'react-native';
import { VOICES, type Voice } from '../constants/voices';
import { Colors, Spacing, FontSize, BorderRadius } from '../constants/theme';

interface VoicePickerProps {
  selected: string;
  onSelect: (voiceName: string) => void;
  /** Filter by language code (optional) */
  filterLanguage?: string;
}

export default function VoicePicker({ selected, onSelect, filterLanguage }: VoicePickerProps) {
  const voices = filterLanguage
    ? VOICES.filter(v => v.originLanguageCode === filterLanguage)
    : VOICES;

  // Group voices by language
  const grouped = voices.reduce<Record<string, Voice[]>>((acc, v) => {
    const key = v.originLanguage;
    if (!acc[key]) acc[key] = [];
    acc[key].push(v);
    return acc;
  }, {});

  const sections = Object.entries(grouped);

  const renderVoice = (voice: Voice) => {
    const isSelected = voice.name === selected;
    return (
      <TouchableOpacity
        key={voice.name}
        style={[styles.voiceCard, isSelected && styles.voiceCardSelected]}
        onPress={() => onSelect(voice.name)}
        activeOpacity={0.7}
      >
        <Text style={styles.genderIcon}>
          {voice.gender === 'female' ? '👩' : '👨'}
        </Text>
        <Text style={[styles.voiceName, isSelected && styles.textSelected]}>
          {voice.name}
        </Text>
        {isSelected && <Text style={styles.check}>✓</Text>}
      </TouchableOpacity>
    );
  };

  return (
    <FlatList
      data={sections}
      keyExtractor={([lang]) => lang}
      renderItem={({ item: [language, langVoices] }) => (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{language}</Text>
          <View style={styles.voiceRow}>
            {langVoices.map(renderVoice)}
          </View>
        </View>
      )}
      showsVerticalScrollIndicator={false}
    />
  );
}

const styles = StyleSheet.create({
  section: {
    marginBottom: Spacing.md,
  },
  sectionTitle: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    fontWeight: '600',
    marginBottom: Spacing.xs,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  voiceRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: Spacing.sm,
  },
  voiceCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Colors.surface,
    borderRadius: BorderRadius.sm,
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.md,
    borderWidth: 1,
    borderColor: Colors.border,
    gap: 6,
  },
  voiceCardSelected: {
    borderColor: Colors.primary,
    backgroundColor: Colors.primaryDark + '30',
  },
  genderIcon: {
    fontSize: 16,
  },
  voiceName: {
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    fontWeight: '500',
  },
  textSelected: {
    color: Colors.primaryLight,
  },
  check: {
    color: Colors.primary,
    fontWeight: '700',
    fontSize: FontSize.md,
  },
});

