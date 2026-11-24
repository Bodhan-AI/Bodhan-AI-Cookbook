// ─────────────────────────────────────────────────────────────
// Book Mode Screen — Sequential page scanning → personal audiobook
// Scan pages of a book and build a translated reading session
// ─────────────────────────────────────────────────────────────

import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  SafeAreaView,
  Alert,
  FlatList,
  ActivityIndicator,
} from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { usePreferences } from '../../src/contexts/PreferencesContext';
import { scanAndTranslate, textToSpeech } from '../../src/api/bodhan';
import { useAudioPlayer } from '../../src/hooks/useAudioPlayer';
import PlayButton from '../../src/components/PlayButton';
import StatusBanner from '../../src/components/StatusBanner';
import { getLanguageName } from '../../src/constants/languages';
import { Colors, Spacing, FontSize, BorderRadius } from '../../src/constants/theme';

interface Page {
  id: number;
  pageNumber: number;
  imageUri: string;
  originalText: string;
  translatedText: string;
  isChapterTitle?: boolean;
  chapterName?: string;
}

interface Book {
  id: number;
  name: string;
  pages: Page[];
  createdAt: Date;
}

export default function BookModeScreen() {
  const { preferences } = usePreferences();
  const player = useAudioPlayer();

  const [books, setBooks] = useState<Book[]>([]);
  const [activeBook, setActiveBook] = useState<Book | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [isNarrating, setIsNarrating] = useState(false);
  const [currentNarrationPage, setCurrentNarrationPage] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const createBook = useCallback(() => {
    Alert.prompt(
      'New Book',
      'Give this book a name:',
      (name) => {
        if (!name?.trim()) return;
        const book: Book = {
          id: Date.now(),
          name: name.trim(),
          pages: [],
          createdAt: new Date(),
        };
        setBooks(prev => [book, ...prev]);
        setActiveBook(book);
      },
      'plain-text',
      '',
      'default'
    );
  }, []);

  const scanPage = useCallback(async () => {
    if (!activeBook) return;

    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.8,
    });

    if (result.canceled || !result.assets[0]) return;

    setIsScanning(true);
    setError(null);

    try {
      const scanResult = await scanAndTranslate(
        result.assets[0].uri,
        preferences.preferredLanguage,
        preferences.scriptMode
      );

      // Detect chapter titles
      const chapterBlock = scanResult.translatedBlocks.find(
        tb => tb.block.label === 'Chapter-title' || tb.block.label === 'Title'
      );

      const page: Page = {
        id: Date.now(),
        pageNumber: activeBook.pages.length + 1,
        imageUri: result.assets[0].uri,
        originalText: scanResult.fullMarkdown,
        translatedText: scanResult.translatedMarkdown,
        isChapterTitle: !!chapterBlock,
        chapterName: chapterBlock?.translation,
      };

      const updatedBook = {
        ...activeBook,
        pages: [...activeBook.pages, page],
      };

      setActiveBook(updatedBook);
      setBooks(prev =>
        prev.map(b => (b.id === updatedBook.id ? updatedBook : b))
      );

      // Auto-read the new page
      if (preferences.autoRead && page.translatedText) {
        try {
          const audioUri = await textToSpeech({
            text: page.translatedText.substring(0, 500),
            voice: preferences.voiceName,
            language: preferences.preferredLanguage,
            style: 'single person narration audiobook',
          });
          await player.play(audioUri);
        } catch {}
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsScanning(false);
    }
  }, [activeBook, preferences, player]);

  const narrateBook = useCallback(async () => {
    if (!activeBook || activeBook.pages.length === 0) return;

    setIsNarrating(true);
    for (let i = currentNarrationPage; i < activeBook.pages.length; i++) {
      setCurrentNarrationPage(i);
      const page = activeBook.pages[i];
      if (!page.translatedText) continue;

      try {
        const audioUri = await textToSpeech({
          text: page.translatedText.substring(0, 500),
          voice: preferences.voiceName,
          language: preferences.preferredLanguage,
          style: 'single person narration audiobook',
        });
        await player.play(audioUri);
        // Wait for playback to finish (simplified — in production use a promise)
        await new Promise(resolve => setTimeout(resolve, 5000));
      } catch {
        break;
      }
    }
    setIsNarrating(false);
  }, [activeBook, currentNarrationPage, preferences, player]);

  // ── Book list view ──
  if (!activeBook) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.header}>
          <Text style={styles.title}>📚 My Books</Text>
          <TouchableOpacity style={styles.newBookButton} onPress={createBook}>
            <Text style={styles.newBookButtonText}>+ New Book</Text>
          </TouchableOpacity>
        </View>

        {books.length === 0 ? (
          <View style={styles.emptyState}>
            <Text style={styles.emptyIcon}>📚</Text>
            <Text style={styles.emptyTitle}>No books yet</Text>
            <Text style={styles.emptyText}>
              Create a book and scan its pages one by one.
              Each page will be translated into {getLanguageName(preferences.preferredLanguage)} and
              narrated like an audiobook.
            </Text>
            <TouchableOpacity style={styles.createButton} onPress={createBook}>
              <Text style={styles.createButtonText}>Create Your First Book</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <FlatList
            data={books}
            keyExtractor={b => b.id.toString()}
            contentContainerStyle={styles.bookList}
            renderItem={({ item: book }) => (
              <TouchableOpacity
                style={styles.bookCard}
                onPress={() => setActiveBook(book)}
              >
                <Text style={styles.bookName}>{book.name}</Text>
                <Text style={styles.bookMeta}>
                  {book.pages.length} pages • {book.createdAt.toLocaleDateString()}
                </Text>
              </TouchableOpacity>
            )}
          />
        )}
      </SafeAreaView>
    );
  }

  // ── Active book view ──
  return (
    <SafeAreaView style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => setActiveBook(null)}>
          <Text style={styles.backText}>← Books</Text>
        </TouchableOpacity>
        <Text style={styles.bookTitle} numberOfLines={1}>
          {activeBook.name}
        </Text>
        <Text style={styles.pageCount}>{activeBook.pages.length} pages</Text>
      </View>

      {/* Error */}
      {error && (
        <View style={{ paddingHorizontal: Spacing.md }}>
          <StatusBanner type="error" message={error} onDismiss={() => setError(null)} />
        </View>
      )}

      {/* Pages */}
      <ScrollView
        style={styles.pagesList}
        contentContainerStyle={styles.pagesContent}
        showsVerticalScrollIndicator={false}
      >
        {activeBook.pages.map((page, index) => (
          <View key={page.id} style={styles.pageCard}>
            <View style={styles.pageHeader}>
              <Text style={styles.pageNumber}>Page {page.pageNumber}</Text>
              {page.isChapterTitle && (
                <Text style={styles.chapterBadge}>📑 {page.chapterName}</Text>
              )}
              <PlayButton
                text={page.translatedText}
                size="small"
              />
            </View>
            <Text style={styles.pageText} selectable numberOfLines={6}>
              {page.translatedText}
            </Text>
          </View>
        ))}

        {activeBook.pages.length === 0 && (
          <View style={styles.emptyPages}>
            <Text style={styles.emptyPagesText}>
              Tap "Scan Next Page" to start adding pages to this book.
            </Text>
          </View>
        )}
      </ScrollView>

      {/* Bottom controls */}
      <View style={styles.bottomBar}>
        {activeBook.pages.length > 0 && (
          <TouchableOpacity
            style={[styles.narrateButton, isNarrating && styles.narrateButtonActive]}
            onPress={isNarrating ? () => { player.stop(); setIsNarrating(false); } : narrateBook}
          >
            <Text style={styles.narrateButtonText}>
              {isNarrating ? '⏹ Stop Narration' : '🎧 Narrate All'}
            </Text>
          </TouchableOpacity>
        )}

        <TouchableOpacity
          style={[styles.scanButton, isScanning && styles.scanButtonDisabled]}
          onPress={scanPage}
          disabled={isScanning}
        >
          {isScanning ? (
            <ActivityIndicator size="small" color={Colors.textOnPrimary} />
          ) : (
            <Text style={styles.scanButtonText}>📷 Scan Next Page</Text>
          )}
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
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
  },
  title: {
    fontSize: FontSize.xl,
    fontWeight: '700',
    color: Colors.textPrimary,
  },
  backText: {
    fontSize: FontSize.md,
    color: Colors.accent,
    fontWeight: '600',
  },
  bookTitle: {
    fontSize: FontSize.lg,
    fontWeight: '700',
    color: Colors.textPrimary,
    flex: 1,
    textAlign: 'center',
    marginHorizontal: Spacing.sm,
  },
  pageCount: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
  },
  newBookButton: {
    backgroundColor: Colors.primary,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    borderRadius: BorderRadius.sm,
  },
  newBookButtonText: {
    color: Colors.textOnPrimary,
    fontSize: FontSize.md,
    fontWeight: '600',
  },
  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
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
    marginBottom: Spacing.lg,
  },
  createButton: {
    backgroundColor: Colors.primary,
    paddingVertical: Spacing.md,
    paddingHorizontal: Spacing.xl,
    borderRadius: BorderRadius.lg,
  },
  createButtonText: {
    color: Colors.textOnPrimary,
    fontSize: FontSize.lg,
    fontWeight: '700',
  },
  bookList: {
    padding: Spacing.md,
  },
  bookCard: {
    backgroundColor: Colors.surface,
    borderRadius: BorderRadius.md,
    padding: Spacing.lg,
    marginBottom: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  bookName: {
    fontSize: FontSize.lg,
    fontWeight: '700',
    color: Colors.textPrimary,
    marginBottom: 4,
  },
  bookMeta: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
  },
  pagesList: {
    flex: 1,
  },
  pagesContent: {
    padding: Spacing.md,
    paddingBottom: Spacing.xxl,
  },
  pageCard: {
    backgroundColor: Colors.surface,
    borderRadius: BorderRadius.md,
    padding: Spacing.md,
    marginBottom: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  pageHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: Spacing.sm,
  },
  pageNumber: {
    fontSize: FontSize.sm,
    fontWeight: '700',
    color: Colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  chapterBadge: {
    fontSize: FontSize.sm,
    color: Colors.accent,
    fontWeight: '600',
    flex: 1,
    marginLeft: Spacing.sm,
  },
  pageText: {
    fontSize: FontSize.base,
    color: Colors.textPrimary,
    lineHeight: 24,
  },
  emptyPages: {
    alignItems: 'center',
    paddingVertical: Spacing.xxl,
  },
  emptyPagesText: {
    fontSize: FontSize.md,
    color: Colors.textSecondary,
    textAlign: 'center',
  },
  bottomBar: {
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.md,
    borderTopWidth: 1,
    borderTopColor: Colors.border,
    gap: Spacing.sm,
  },
  narrateButton: {
    paddingVertical: Spacing.sm + 4,
    borderRadius: BorderRadius.md,
    alignItems: 'center',
    backgroundColor: Colors.surfaceLight,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  narrateButtonActive: {
    borderColor: Colors.accentRed,
  },
  narrateButtonText: {
    color: Colors.textPrimary,
    fontSize: FontSize.md,
    fontWeight: '600',
  },
  scanButton: {
    paddingVertical: Spacing.md,
    borderRadius: BorderRadius.lg,
    alignItems: 'center',
    backgroundColor: Colors.primary,
  },
  scanButtonDisabled: {
    opacity: 0.5,
  },
  scanButtonText: {
    color: Colors.textOnPrimary,
    fontSize: FontSize.lg,
    fontWeight: '700',
  },
});

