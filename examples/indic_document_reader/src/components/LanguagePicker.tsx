// ─────────────────────────────────────────────────────────────
// LanguagePicker — Grid-based language selector
// Used in onboarding and settings
// ─────────────────────────────────────────────────────────────

import React, { useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  TextInput,
  FlatList,
  StyleSheet,
} from 'react-native';
import { TRANSLATION_LANGUAGES, type Language } from '../constants/languages';
import { Colors, Spacing, FontSize, BorderRadius } from '../constants/theme';

interface LanguagePickerProps {
  /** Currently selected language code */
  selected: string;
  /** Called when a language is selected */
  onSelect: (code: string) => void;
  /** Optional: filter to only show certain languages */
  languages?: Language[];
  /** Show search bar */
  showSearch?: boolean;
}

export default function LanguagePicker({
  selected,
  onSelect,
  languages = TRANSLATION_LANGUAGES,
  showSearch = true,
}: LanguagePickerProps) {
  const [search, setSearch] = useState('');

  const filtered = search
    ? languages.filter(
        l =>
          l.name.toLowerCase().includes(search.toLowerCase()) ||
          l.nativeName.includes(search) ||
          l.code.toLowerCase().includes(search.toLowerCase())
      )
    : languages;

  const renderItem = ({ item }: { item: Language }) => {
    const isSelected = item.code === selected;
    return (
      <TouchableOpacity
        style={[styles.card, isSelected && styles.cardSelected]}
        onPress={() => onSelect(item.code)}
        activeOpacity={0.7}
      >
        <Text style={[styles.nativeName, isSelected && styles.textSelected]}>
          {item.nativeName}
        </Text>
        <Text style={[styles.name, isSelected && styles.textSelected]}>
          {item.name}
        </Text>
        {isSelected && <Text style={styles.checkmark}>✓</Text>}
      </TouchableOpacity>
    );
  };

  return (
    <View style={styles.container}>
      {showSearch && (
        <TextInput
          style={styles.searchInput}
          placeholder="Search languages..."
          placeholderTextColor={Colors.textMuted}
          value={search}
          onChangeText={setSearch}
          autoCorrect={false}
        />
      )}
      <FlatList
        data={filtered}
        renderItem={renderItem}
        keyExtractor={item => item.code}
        numColumns={2}
        columnWrapperStyle={styles.row}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.list}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  searchInput: {
    backgroundColor: Colors.surfaceLight,
    color: Colors.textPrimary,
    borderRadius: BorderRadius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 4,
    fontSize: FontSize.base,
    marginBottom: Spacing.md,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  list: {
    paddingBottom: Spacing.xl,
  },
  row: {
    justifyContent: 'space-between',
    marginBottom: Spacing.sm,
  },
  card: {
    flex: 0.48,
    backgroundColor: Colors.surface,
    borderRadius: BorderRadius.md,
    padding: Spacing.md,
    borderWidth: 1.5,
    borderColor: Colors.border,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 80,
    position: 'relative',
  },
  cardSelected: {
    borderColor: Colors.primary,
    backgroundColor: Colors.primaryDark + '30',
  },
  nativeName: {
    fontSize: FontSize.lg,
    fontWeight: '700',
    color: Colors.textPrimary,
    marginBottom: 4,
    textAlign: 'center',
  },
  name: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    textAlign: 'center',
  },
  textSelected: {
    color: Colors.primaryLight,
  },
  checkmark: {
    position: 'absolute',
    top: 6,
    right: 10,
    fontSize: FontSize.base,
    color: Colors.primary,
    fontWeight: '700',
  },
});

