// ─────────────────────────────────────────────────────────────
// PlayButton — TTS playback control with loading state
// ─────────────────────────────────────────────────────────────

import React, { useCallback, useState } from 'react';
import { TouchableOpacity, Text, ActivityIndicator, StyleSheet, View } from 'react-native';
import { textToSpeech } from '../api/bodhan';
import { useAudioPlayer } from '../hooks/useAudioPlayer';
import { usePreferences } from '../contexts/PreferencesContext';
import { Colors, Spacing, FontSize, BorderRadius } from '../constants/theme';
import type { TTSStyle } from '../constants/languages';

interface PlayButtonProps {
  /** Text to speak */
  text: string;
  /** Language of the text (defaults to user's preferred language) */
  language?: string;
  /** Override voice name */
  voice?: string;
  /** Override style */
  style?: TTSStyle | 'neutral';
  /** Button size variant */
  size?: 'small' | 'medium' | 'large';
  /** Show label alongside icon */
  showLabel?: boolean;
}

export default function PlayButton({
  text,
  language,
  voice,
  style: ttsStyle,
  size = 'medium',
  showLabel = false,
}: PlayButtonProps) {
  const { preferences } = usePreferences();
  const player = useAudioPlayer();
  const [isGenerating, setIsGenerating] = useState(false);

  const resolvedLang = language ?? preferences.preferredLanguage;
  const resolvedVoice = voice ?? preferences.voiceName;
  const resolvedStyle = ttsStyle ?? preferences.ttsStyle;

  const handlePress = useCallback(async () => {
    if (player.isPlaying) {
      await player.stop();
      return;
    }

    if (!text || text.trim().length === 0) return;

    try {
      setIsGenerating(true);
      const audioUri = await textToSpeech({
        text: text.substring(0, 500), // Keep within reasonable TTS limits
        voice: resolvedVoice,
        language: resolvedLang,
        style: resolvedStyle === 'neutral' ? undefined : resolvedStyle,
      });
      setIsGenerating(false);
      await player.play(audioUri);
    } catch (error) {
      setIsGenerating(false);
      console.error('TTS error:', error);
    }
  }, [text, resolvedVoice, resolvedLang, resolvedStyle, player]);

  const isLoading = isGenerating || player.isLoading;
  const iconSize = size === 'large' ? 32 : size === 'medium' ? 24 : 18;
  const buttonSize = size === 'large' ? 64 : size === 'medium' ? 48 : 36;

  return (
    <TouchableOpacity
      style={[
        styles.button,
        {
          width: showLabel ? undefined : buttonSize,
          height: buttonSize,
          borderRadius: showLabel ? BorderRadius.lg : buttonSize / 2,
          paddingHorizontal: showLabel ? Spacing.md : 0,
        },
        player.isPlaying && styles.buttonPlaying,
      ]}
      onPress={handlePress}
      disabled={isLoading || !text}
      activeOpacity={0.7}
    >
      {isLoading ? (
        <ActivityIndicator size="small" color={Colors.textOnPrimary} />
      ) : (
        <View style={styles.content}>
          <Text style={[styles.icon, { fontSize: iconSize }]}>
            {player.isPlaying ? '⏹' : '▶'}
          </Text>
          {showLabel && (
            <Text style={styles.label}>
              {player.isPlaying ? 'Stop' : 'Listen'}
            </Text>
          )}
        </View>
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  button: {
    backgroundColor: Colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    elevation: 4,
    shadowColor: Colors.primary,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 4,
  },
  buttonPlaying: {
    backgroundColor: Colors.accentRed,
  },
  content: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  icon: {
    color: Colors.textOnPrimary,
  },
  label: {
    color: Colors.textOnPrimary,
    fontSize: FontSize.md,
    fontWeight: '600',
  },
});

