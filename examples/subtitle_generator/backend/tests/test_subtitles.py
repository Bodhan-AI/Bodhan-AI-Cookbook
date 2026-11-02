import unittest

from app.subtitles import Segment, clean_transcript, lines_from_words, split_into_lines, srt_timestamp, time_lines, to_srt


class SrtTests(unittest.TestCase):
    def test_timestamp_format(self):
        self.assertEqual(srt_timestamp(0), "00:00:00,000")
        self.assertEqual(srt_timestamp(1.2345), "00:00:01,235")  # rounds half up
        self.assertEqual(srt_timestamp(3661.234), "01:01:01,234")
        self.assertEqual(srt_timestamp(-5.0), "00:00:00,000")

    def test_numbering_order_and_blank_lines(self):
        srt = to_srt([Segment(start=0, end=1.25, text="Hello"), Segment(start=1.25, end=2.5, text="World")])
        self.assertEqual(srt, "1\n00:00:00,000 --> 00:00:01,250\nHello\n\n2\n00:00:01,250 --> 00:00:02,500\nWorld\n")

    def test_text_cannot_break_the_format(self):
        srt = to_srt([Segment(start=0, end=2, text="Press A --> B")])
        self.assertIn("Press A → B", srt)
        self.assertEqual(srt.count("-->"), 1)

    def test_commas_in_dialogue_are_kept(self):
        dialogue = "Yes, of course, that's 1,000 reasons."
        self.assertIn(dialogue, to_srt([Segment(start=1.25, end=3.75, text=dialogue)]))

    def test_end_before_start_is_corrected_and_empty_is_empty(self):
        self.assertIn("00:00:05,000 --> 00:00:05,100", to_srt([Segment(start=5, end=4, text="Flash")]))
        self.assertEqual(to_srt([]), "")


class TranscriptTests(unittest.TestCase):
    def test_repeated_junk_is_removed(self):
        # Seen live on the cookbook's sample_hindi.wav: real speech followed by U+0B44 repeated ~160 times
        self.assertEqual(clean_transcript("कृपया अपनी पुस्तकें खोलिए" + "ୄ" * 160), "कृपया अपनी पुस्तकें खोलिए")
        self.assertEqual(clean_transcript("ୄ" * 40), "")
        self.assertEqual(clean_transcript("हाहाहाहा, नहीं नहीं नहीं"), "हाहाहाहा, नहीं नहीं नहीं")

    def test_repeated_groups_of_junk_are_removed(self):
        # Seen live on English speech: a group of letters repeated to the end of the transcript
        junk = "Nobody else is doing it for the U S " + "D N R E " * 20
        self.assertEqual(clean_transcript(junk), "Nobody else is doing it for the U S")
        self.assertEqual(clean_transcript("ठीक है " + "ab" * 30), "ठीक है")

    def test_real_repetition_is_kept(self):
        # Lyrics and speech repeat words and phrases a few times; that is not junk
        for text in (
            "लव तुझे लव मैं करता हूँ लव तुझे लव मैं करता हूँ",
            "ना ना ना ना ना",
            "no no no no no no no",  # 7 times: still kept
            "तुझसे बिछड़ने से डरती हूँ तुझसे बिछड़ने से डरती हूँ",
        ):
            self.assertEqual(clean_transcript(text), text)

    def test_long_text_becomes_short_lines(self):
        text = "नमस्ते दोस्तों आज के इस वीडियो में हम बात करेंगे कि कैसे आप अपने चैनल को ग्रो कर सकते हैं और ज्यादा से ज्यादा व्यूज पा सकते हैं"
        lines = split_into_lines(text)
        self.assertGreaterEqual(len(lines), 4)
        self.assertTrue(all(len(line.split()) <= 10 for line in lines))

    def test_one_short_sentence_fills_its_speech(self):
        segments = time_lines("नमस्ते, स्वागत है।", [(0.0, 5.0)])
        self.assertEqual([(s.start, s.end) for s in segments], [(0.0, 5.0)])

    def test_lines_spread_over_continuous_speech(self):
        text = "पहला वाक्य यहाँ है। दूसरा वाक्य थोड़ा लंबा है और यहाँ समाप्त होता है। तीसरा वाक्य।"
        segments = time_lines(text, [(10.0, 30.0)])
        self.assertGreaterEqual(len(segments), 3)
        self.assertEqual((segments[0].start, segments[-1].end), (10.0, 30.0))
        for a, b in zip(segments, segments[1:], strict=False):
            self.assertEqual(a.end, b.start)

    def test_pauses_get_no_subtitle(self):
        # Two equal sentences spoken 0-4 s and 8-12 s, with silence between
        segments = time_lines("यह पहला वाक्य है। यह दूसरा वाक्य है।", [(0.0, 4.0), (8.0, 12.0)])
        self.assertEqual([(s.start, s.end) for s in segments], [(0.0, 4.0), (8.0, 12.0)])

    def test_a_line_can_span_a_pause(self):
        # One line for both stretches: it starts with the first and ends with the second
        segments = time_lines("नमस्ते", [(1.0, 2.0), (5.0, 6.0)])
        self.assertEqual((segments[0].start, segments[0].end), (1.0, 6.0))

    def test_lines_from_word_timestamps(self):
        words = [
            {"word": "नमस्ते", "start": 0.0, "end": 0.5},
            {"word": "दोस्तों।", "start": 0.6, "end": 1.1},
            {"word": "आज", "start": 2.5, "end": 2.8},  # a 1.4 s pause starts a new line anyway
            {"word": "ୄ" * 30, "start": 2.9, "end": 3.0},  # junk is dropped
            {"word": "गणित", "start": 3.1, "end": 3.6},
        ]
        segments = lines_from_words(words, offset=10.0)
        self.assertEqual([(s.start, s.end, s.text) for s in segments], [(10.0, 11.1, "नमस्ते दोस्तों।"), (12.5, 13.6, "आज गणित")])


if __name__ == "__main__":
    unittest.main()
