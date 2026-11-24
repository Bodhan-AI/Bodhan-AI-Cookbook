// ─────────────────────────────────────────────────────────────
// Camera Reader Screen — Scan → OCR → Translate → Read Aloud
// The core feature: point camera at any text and understand it
// ─────────────────────────────────────────────────────────────

import React, { useState, useRef, useCallback } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  SafeAreaView,
  Image,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as ImagePicker from 'expo-image-picker';
import { usePreferences } from '../../src/contexts/PreferencesContext';
import { scanAndTranslate, textToSpeech, type OCRBlock } from '../../src/api/bodhan';
import TranslatedBlock from '../../src/components/TranslatedBlock';
import ScriptToggle from '../../src/components/ScriptToggle';
import PlayButton from '../../src/components/PlayButton';
import StatusBanner from '../../src/components/StatusBanner';
import { getLanguageName } from '../../src/constants/languages';
import type { ScriptMode } from '../../src/constants/languages';
import { Colors, Spacing, FontSize, BorderRadius } from '../../src/constants/theme';

type ScreenState = 'camera' | 'processing' | 'results';

interface TranslatedResult {
  block: OCRBlock;
  translation: string;
  truncated: boolean;
}

export default function CameraScreen() {
  const { preferences } = usePreferences();
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);

  const [screenState, setScreenState] = useState<ScreenState>('camera');
  const [capturedImage, setCapturedImage] = useState<string | null>(null);
  const [translatedBlocks, setTranslatedBlocks] = useState<TranslatedResult[]>([]);
  const [originalMarkdown, setOriginalMarkdown] = useState('');
  const [translatedMarkdown, setTranslatedMarkdown] = useState('');
  const [scriptMode, setScriptMode] = useState<ScriptMode>(preferences.scriptMode);
  const [processingStep, setProcessingStep] = useState('');
  const [error, setError] = useState<string | null>(null);

  const processImage = useCallback(async (imageUri: string) => {
    setScreenState('processing');
    setCapturedImage(imageUri);
    setError(null);

    try {
      setProcessingStep('Extracting text from image...');
      const result = await scanAndTranslate(
        imageUri,
        preferences.preferredLanguage,
        scriptMode
      );

      setTranslatedBlocks(result.translatedBlocks);
      setOriginalMarkdown(result.fullMarkdown);
      setTranslatedMarkdown(result.translatedMarkdown);
      setScreenState('results');
    } catch (err) {
      const message = (err as Error).message;
      setError(message);
      setScreenState('camera');
      Alert.alert('Error', message);
    }
  }, [preferences.preferredLanguage, scriptMode]);

  const handleCapture = useCallback(async () => {
    if (!cameraRef.current) return;

    try {
      const photo = await cameraRef.current.takePictureAsync({
        quality: 0.8,
        base64: false,
      });
      if (photo?.uri) {
        processImage(photo.uri);
      }
    } catch (err) {
      Alert.alert('Error', 'Failed to capture image');
    }
  }, [processImage]);

  const handlePickImage = useCallback(async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.8,
    });

    if (!result.canceled && result.assets[0]) {
      processImage(result.assets[0].uri);
    }
  }, [processImage]);

  const handleRetake = useCallback(() => {
    setScreenState('camera');
    setCapturedImage(null);
    setTranslatedBlocks([]);
    setError(null);
  }, []);

  // ── Permission check ──
  if (!permission) return <View style={styles.container} />;
  if (!permission.granted) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.permissionContainer}>
          <Text style={styles.permissionIcon}>📷</Text>
          <Text style={styles.permissionTitle}>Camera Access Needed</Text>
          <Text style={styles.permissionText}>
            Bodhan Reader needs camera access to scan book pages and documents for translation.
          </Text>
          <TouchableOpacity style={styles.permissionButton} onPress={requestPermission}>
            <Text style={styles.permissionButtonText}>Grant Access</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.galleryLink} onPress={handlePickImage}>
            <Text style={styles.galleryLinkText}>Or pick from gallery →</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  // ── Processing state ──
  if (screenState === 'processing') {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.processingContainer}>
          {capturedImage && (
            <Image source={{ uri: capturedImage }} style={styles.previewImage} />
          )}
          <ActivityIndicator size="large" color={Colors.primary} />
          <Text style={styles.processingText}>{processingStep}</Text>
          <Text style={styles.processingSubtext}>
            Translating into {getLanguageName(preferences.preferredLanguage)}...
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  // ── Results state ──
  if (screenState === 'results') {
    return (
      <SafeAreaView style={styles.container}>
        {/* Header */}
        <View style={styles.resultsHeader}>
          <TouchableOpacity onPress={handleRetake} style={styles.backButton}>
            <Text style={styles.backButtonText}>← Scan Again</Text>
          </TouchableOpacity>
          <Text style={styles.resultsTitle}>
            {getLanguageName(preferences.preferredLanguage)}
          </Text>
          <PlayButton
            text={translatedMarkdown}
            size="small"
          />
        </View>

        {/* Script toggle */}
        <View style={styles.scriptToggleRow}>
          <ScriptToggle
            value={scriptMode}
            onChange={setScriptMode}
            disableCodemix={preferences.preferredLanguage === 'en'}
            disableRoman={preferences.preferredLanguage === 'en'}
          />
        </View>

        {/* Translated blocks */}
        <ScrollView
          style={styles.resultsList}
          contentContainerStyle={styles.resultsContent}
          showsVerticalScrollIndicator={false}
        >
          {capturedImage && (
            <Image source={{ uri: capturedImage }} style={styles.thumbnailImage} />
          )}

          {translatedBlocks.map((tb, index) => (
            <TranslatedBlock
              key={`${tb.block.order}-${index}`}
              block={tb.block}
              translation={tb.translation}
              language={preferences.preferredLanguage}
            />
          ))}

          {translatedBlocks.some(tb => tb.truncated) && (
            <StatusBanner
              type="info"
              message="Some translations were truncated"
              detail="The text was too long for a single translation. Consider scanning smaller sections."
            />
          )}
        </ScrollView>
      </SafeAreaView>
    );
  }

  // ── Camera state ──
  return (
    <View style={styles.container}>
      <CameraView
        ref={cameraRef}
        style={styles.camera}
        facing="back"
      >
        {/* Overlay UI */}
        <SafeAreaView style={styles.cameraOverlay}>
          {/* Top bar */}
          <View style={styles.cameraTopBar}>
            <Text style={styles.cameraTitle}>📷 Camera Reader</Text>
            <Text style={styles.cameraLang}>
              → {getLanguageName(preferences.preferredLanguage)}
            </Text>
          </View>

          {/* Guide frame */}
          <View style={styles.guideFrame}>
            <View style={styles.guideCornerTL} />
            <View style={styles.guideCornerTR} />
            <View style={styles.guideCornerBL} />
            <View style={styles.guideCornerBR} />
            <Text style={styles.guideText}>
              Position the text inside the frame
            </Text>
          </View>

          {/* Bottom controls */}
          <View style={styles.cameraBottomBar}>
            <TouchableOpacity style={styles.galleryButton} onPress={handlePickImage}>
              <Text style={styles.galleryButtonIcon}>🖼️</Text>
              <Text style={styles.galleryButtonText}>Gallery</Text>
            </TouchableOpacity>

            <TouchableOpacity style={styles.captureButton} onPress={handleCapture}>
              <View style={styles.captureButtonInner} />
            </TouchableOpacity>

            <View style={styles.galleryButton}>
              <Text style={styles.galleryButtonIcon}>⚡</Text>
              <Text style={styles.galleryButtonText}>Auto</Text>
            </View>
          </View>
        </SafeAreaView>
      </CameraView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  // Camera
  camera: {
    flex: 1,
  },
  cameraOverlay: {
    flex: 1,
    justifyContent: 'space-between',
  },
  cameraTopBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.md,
    backgroundColor: Colors.overlay,
  },
  cameraTitle: {
    fontSize: FontSize.lg,
    fontWeight: '700',
    color: Colors.textPrimary,
  },
  cameraLang: {
    fontSize: FontSize.md,
    color: Colors.accent,
    fontWeight: '600',
  },
  guideFrame: {
    marginHorizontal: Spacing.xl,
    aspectRatio: 0.75,
    justifyContent: 'center',
    alignItems: 'center',
    position: 'relative',
  },
  guideCornerTL: {
    position: 'absolute', top: 0, left: 0, width: 30, height: 30,
    borderTopWidth: 3, borderLeftWidth: 3, borderColor: Colors.primary,
  },
  guideCornerTR: {
    position: 'absolute', top: 0, right: 0, width: 30, height: 30,
    borderTopWidth: 3, borderRightWidth: 3, borderColor: Colors.primary,
  },
  guideCornerBL: {
    position: 'absolute', bottom: 0, left: 0, width: 30, height: 30,
    borderBottomWidth: 3, borderLeftWidth: 3, borderColor: Colors.primary,
  },
  guideCornerBR: {
    position: 'absolute', bottom: 0, right: 0, width: 30, height: 30,
    borderBottomWidth: 3, borderRightWidth: 3, borderColor: Colors.primary,
  },
  guideText: {
    color: Colors.textSecondary,
    fontSize: FontSize.sm,
    textAlign: 'center',
    backgroundColor: Colors.overlay,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.xs,
    borderRadius: BorderRadius.sm,
  },
  cameraBottomBar: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    alignItems: 'center',
    paddingVertical: Spacing.lg,
    backgroundColor: Colors.overlay,
  },
  captureButton: {
    width: 72,
    height: 72,
    borderRadius: 36,
    borderWidth: 4,
    borderColor: Colors.textPrimary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  captureButtonInner: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: Colors.textPrimary,
  },
  galleryButton: {
    alignItems: 'center',
    width: 60,
  },
  galleryButtonIcon: {
    fontSize: 24,
  },
  galleryButtonText: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
    marginTop: 4,
  },
  // Permission
  permissionContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: Spacing.xl,
  },
  permissionIcon: {
    fontSize: 64,
    marginBottom: Spacing.lg,
  },
  permissionTitle: {
    fontSize: FontSize.xl,
    fontWeight: '700',
    color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
  permissionText: {
    fontSize: FontSize.base,
    color: Colors.textSecondary,
    textAlign: 'center',
    marginBottom: Spacing.lg,
    lineHeight: 24,
  },
  permissionButton: {
    backgroundColor: Colors.primary,
    paddingVertical: Spacing.md,
    paddingHorizontal: Spacing.xl,
    borderRadius: BorderRadius.lg,
    marginBottom: Spacing.md,
  },
  permissionButtonText: {
    color: Colors.textOnPrimary,
    fontSize: FontSize.lg,
    fontWeight: '700',
  },
  galleryLink: {
    padding: Spacing.sm,
  },
  galleryLinkText: {
    color: Colors.accent,
    fontSize: FontSize.md,
  },
  // Processing
  processingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: Spacing.xl,
    gap: Spacing.lg,
  },
  previewImage: {
    width: 200,
    height: 280,
    borderRadius: BorderRadius.md,
    marginBottom: Spacing.md,
  },
  processingText: {
    fontSize: FontSize.lg,
    fontWeight: '600',
    color: Colors.textPrimary,
  },
  processingSubtext: {
    fontSize: FontSize.md,
    color: Colors.textSecondary,
  },
  // Results
  resultsHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
  },
  backButton: {
    padding: Spacing.sm,
  },
  backButtonText: {
    color: Colors.accent,
    fontSize: FontSize.md,
    fontWeight: '600',
  },
  resultsTitle: {
    fontSize: FontSize.lg,
    fontWeight: '700',
    color: Colors.textPrimary,
  },
  scriptToggleRow: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
  },
  resultsList: {
    flex: 1,
  },
  resultsContent: {
    padding: Spacing.md,
    paddingBottom: Spacing.xxl,
  },
  thumbnailImage: {
    width: '100%',
    height: 160,
    borderRadius: BorderRadius.md,
    marginBottom: Spacing.md,
    resizeMode: 'cover',
  },
});

