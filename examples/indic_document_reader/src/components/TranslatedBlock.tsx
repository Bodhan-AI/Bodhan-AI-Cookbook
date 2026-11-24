// ─────────────────────────────────────────────────────────────
// TranslatedBlock — Renders a translated text block from OCR
// Shows original + translation with layout awareness
// ─────────────────────────────────────────────────────────────

import React, { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import type { OCRBlock } from '../api/bodhan';
import PlayButton from './PlayButton';
import { Colors, Spacing, FontSize, BorderRadius } from '../constants/theme';

interface TranslatedBlockProps {
  block: OCRBlock;
  translation: string;
  /** Show original text alongside translation */
  showOriginal?: boolean;
  /** Language of the translation */
  language: string;
}

export default function TranslatedBlock({
  block,
  translation,
  showOriginal = false,
  language,
}: TranslatedBlockProps) {
  const [expanded, setExpanded] = useState(showOriginal);

  // Determine text style based on block type
  const isHeading = ['Title', 'SectionHeader'].includes(block.type) ||
    ['Chapter-title', 'Section-title', 'Sub-section-title', 'Title'].includes(block.label);
  const isCaption = block.type === 'Caption' || block.label.includes('caption');
  const isTable = block.type === 'Table' || block.label === 'Table';

  return (
    <View style={[styles.container, isHeading && styles.headingContainer]}>
      {/* Translation */}
      <Text
        style={[
          styles.translationText,
          isHeading && styles.headingText,
          isCaption && styles.captionText,
          isTable && styles.tableText,
        ]}
        selectable
      >
        {translation}
      </Text>

      {/* Controls row */}
      <View style={styles.controls}>
        <PlayButton text={translation} language={language} size="small" />

        <TouchableOpacity
          onPress={() => setExpanded(!expanded)}
          style={styles.toggleButton}
        >
          <Text style={styles.toggleText}>
            {expanded ? 'Hide original' : 'Show original'}
          </Text>
        </TouchableOpacity>

        <Text style={styles.blockLabel}>{block.label}</Text>
      </View>

      {/* Original text (collapsible) */}
      {expanded && block.text ? (
        <View style={styles.originalContainer}>
          <Text style={styles.originalLabel}>Original:</Text>
          <Text style={styles.originalText} selectable>
            {block.text}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: Colors.surface,
    borderRadius: BorderRadius.md,
    padding: Spacing.md,
    marginBottom: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  headingContainer: {
    borderLeftWidth: 3,
    borderLeftColor: Colors.primary,
  },
  translationText: {
    fontSize: FontSize.base,
    color: Colors.textPrimary,
    lineHeight: 24,
  },
  headingText: {
    fontSize: FontSize.xl,
    fontWeight: '700',
    lineHeight: 32,
  },
  captionText: {
    fontSize: FontSize.sm,
    fontStyle: 'italic',
    color: Colors.textSecondary,
  },
  tableText: {
    fontFamily: 'monospace',
    fontSize: FontSize.sm,
  },
  controls: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: Spacing.sm,
    gap: Spacing.sm,
  },
  toggleButton: {
    paddingVertical: 4,
    paddingHorizontal: 8,
  },
  toggleText: {
    fontSize: FontSize.xs,
    color: Colors.accent,
  },
  blockLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginLeft: 'auto',
  },
  originalContainer: {
    marginTop: Spacing.sm,
    paddingTop: Spacing.sm,
    borderTopWidth: 1,
    borderTopColor: Colors.border,
  },
  originalLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginBottom: 4,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  originalText: {
    fontSize: FontSize.md,
    color: Colors.textSecondary,
    lineHeight: 22,
  },
});

