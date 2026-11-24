// ─────────────────────────────────────────────────────────────
// Root Layout — Entry point for the app
// Wraps everything with PreferencesProvider
// Routes to onboarding or main tabs based on setup state
// ─────────────────────────────────────────────────────────────

import React, { useEffect } from 'react';
import { View, ActivityIndicator, StyleSheet } from 'react-native';
import { Stack, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { PreferencesProvider, usePreferences } from '../src/contexts/PreferencesContext';
import { setApiKey } from '../src/api/bodhan';
import { Colors } from '../src/constants/theme';

function RootNavigator() {
  const { preferences, isLoading } = usePreferences();
  const router = useRouter();
  const segments = useSegments();

  useEffect(() => {
    if (isLoading) return;

    // Set API key from preferences
    if (preferences.apiKey) {
      setApiKey(preferences.apiKey);
    }

    const inOnboarding = segments[0] === 'onboarding';

    if (!preferences.onboardingComplete && !inOnboarding) {
      // User hasn't completed onboarding → redirect
      router.replace('/onboarding');
    } else if (preferences.onboardingComplete && inOnboarding) {
      // User completed onboarding but somehow on onboarding screen → go to tabs
      router.replace('/(tabs)/camera');
    }
  }, [isLoading, preferences.onboardingComplete, segments]);

  if (isLoading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color={Colors.primary} />
      </View>
    );
  }

  return (
    <>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: Colors.background },
          animation: 'fade',
        }}
      >
        <Stack.Screen name="onboarding" />
        <Stack.Screen name="(tabs)" />
      </Stack>
    </>
  );
}

export default function RootLayout() {
  return (
    <PreferencesProvider>
      <RootNavigator />
    </PreferencesProvider>
  );
}

const styles = StyleSheet.create({
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: Colors.background,
  },
});

