// ─────────────────────────────────────────────────────────────
// ScriptToggle — Toggle between native / roman / codemix
// ─────────────────────────────────────────────────────────────

import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import type { ScriptMode } from '../constants/languages';
import { Colors, Spacing, FontSize, BorderRadius } from '../constants/theme';

interface ScriptToggleProps {
  value: ScriptMode;
  onChange: (mode: ScriptMode) => void;
  /** Disable codemix option (e.g., for English target) */
  disableCodemix?: boolean;
  /** Disable roman option (e.g., for English target) */
  disableRoman?: boolean;
}

const OPTIONS: { value: ScriptMode; label: string; icon: string }[] = [
  { value: 'native', label: 'Native', icon: 'अ' },
  { value: 'roman', label: 'Roman', icon: 'A' },
  { value: 'codemix', label: 'Mix', icon: 'अA' },
];

export default function ScriptToggle({
  value,
  onChange,
  disableCodemix = false,
  disableRoman = false,
}: ScriptToggleProps) {
  return (
    <View style={styles.container}>
      {OPTIONS.map(option => {
        const isDisabled =
          (option.value === 'codemix' && disableCodemix) ||
          (option.value === 'roman' && disableRoman);
        const isActive = option.value === value;

        return (
          <TouchableOpacity
            key={option.value}
            style={[
              styles.option,
              isActive && styles.optionActive,
              isDisabled && styles.optionDisabled,
            ]}
            onPress={() => !isDisabled && onChange(option.value)}
            disabled={isDisabled}
            activeOpacity={0.7}
          >
            <Text style={[styles.icon, isActive && styles.textActive, isDisabled && styles.textDisabled]}>
              {option.icon}
            </Text>
            <Text style={[styles.label, isActive && styles.textActive, isDisabled && styles.textDisabled]}>
              {option.label}
            </Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    backgroundColor: Colors.surface,
    borderRadius: BorderRadius.lg,
    padding: 3,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  option: {
    flex: 1,
    paddingVertical: Spacing.sm,
    alignItems: 'center',
    borderRadius: BorderRadius.md,
  },
  optionActive: {
    backgroundColor: Colors.primary,
  },
  optionDisabled: {
    opacity: 0.3,
  },
  icon: {
    fontSize: FontSize.base,
    color: Colors.textSecondary,
    fontWeight: '700',
  },
  label: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
    marginTop: 2,
  },
  textActive: {
    color: Colors.textOnPrimary,
  },
  textDisabled: {
    color: Colors.textMuted,
  },
});

