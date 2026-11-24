// ─────────────────────────────────────────────────────────────
// StatusBanner — Displays loading/error/success states
// ─────────────────────────────────────────────────────────────

import React from 'react';
import { View, Text, ActivityIndicator, TouchableOpacity, StyleSheet } from 'react-native';
import { Colors, Spacing, FontSize, BorderRadius } from '../constants/theme';

type BannerType = 'loading' | 'error' | 'success' | 'info';

interface StatusBannerProps {
  type: BannerType;
  message: string;
  /** Optional sub-message / details */
  detail?: string;
  /** Show dismiss button */
  onDismiss?: () => void;
  /** Show retry button */
  onRetry?: () => void;
}

const BANNER_CONFIG: Record<BannerType, { bg: string; icon: string; iconColor: string }> = {
  loading: { bg: Colors.surface, icon: '', iconColor: Colors.accent },
  error: { bg: '#2D1515', icon: '⚠️', iconColor: Colors.error },
  success: { bg: '#152D1A', icon: '✅', iconColor: Colors.success },
  info: { bg: Colors.surface, icon: 'ℹ️', iconColor: Colors.info },
};

export default function StatusBanner({ type, message, detail, onDismiss, onRetry }: StatusBannerProps) {
  const config = BANNER_CONFIG[type];

  return (
    <View style={[styles.container, { backgroundColor: config.bg }]}>
      <View style={styles.content}>
        {type === 'loading' ? (
          <ActivityIndicator size="small" color={config.iconColor} />
        ) : (
          <Text style={styles.icon}>{config.icon}</Text>
        )}
        <View style={styles.textContainer}>
          <Text style={styles.message}>{message}</Text>
          {detail && <Text style={styles.detail}>{detail}</Text>}
        </View>
      </View>
      <View style={styles.actions}>
        {onRetry && (
          <TouchableOpacity onPress={onRetry} style={styles.actionButton}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        )}
        {onDismiss && (
          <TouchableOpacity onPress={onDismiss} style={styles.dismissButton}>
            <Text style={styles.dismissText}>✕</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: Spacing.md,
    borderRadius: BorderRadius.md,
    marginBottom: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  content: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    gap: Spacing.sm,
  },
  icon: {
    fontSize: 18,
  },
  textContainer: {
    flex: 1,
  },
  message: {
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    fontWeight: '500',
  },
  detail: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    marginTop: 2,
  },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.sm,
  },
  actionButton: {
    paddingVertical: 4,
    paddingHorizontal: 10,
    backgroundColor: Colors.primary,
    borderRadius: BorderRadius.sm,
  },
  retryText: {
    color: Colors.textOnPrimary,
    fontSize: FontSize.sm,
    fontWeight: '600',
  },
  dismissButton: {
    padding: 4,
  },
  dismissText: {
    color: Colors.textMuted,
    fontSize: FontSize.base,
  },
});

