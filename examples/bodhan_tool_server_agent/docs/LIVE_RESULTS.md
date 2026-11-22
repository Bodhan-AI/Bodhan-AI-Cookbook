# Live results

Run 2026-11-20 17:05; real Bodhan APIs; open test data. Similarity = character-trigram F1 vs reference text (1.0 = identical); wer/cer = word/character error rate vs the dataset's reference text (wer is inflated where the reference splits compound words differently; cer is the fairer figure for Indic scripts). Costs are what the ledger recorded (cached items read Rs 0).

| Check | Result | Detail | Rs |
|---|---|---|---|
| transcribe hi_3min.wav (unhinted) | PASS | 8 chunks, lang=hi (words), sim=0.96, warnings=[], wer=7.0%, cer=2.2% | 0.000 |
| transcribe ta 25 s clip (unhinted) | PASS | lang=ta, chunks=1, sim=0.98, wer=6.7%, cer=1.5% | 0.000 |
| language ID te_in | PASS | detected=te conf=0.9 alts=[], sim=1.00, wer=20.0%, cer=0.0% | 0.000 |
| language ID kn_in | PASS | detected=kn conf=0.9 alts=[], sim=0.87, wer=13.3%, cer=14.1% | 0.000 |
| language ID ml_in | PASS | detected=ml conf=0.9 alts=[], sim=0.97, wer=21.4%, cer=0.9% | 0.000 |
| language ID mr_in | PASS | detected=mr conf=0.88 alts=['hi'], sim=0.96, wer=20.0%, cer=1.3% | 0.000 |
| language ID ne_np | PASS | detected=ne conf=0.95 alts=[], sim=0.92, wer=13.3%, cer=7.0% | 0.000 |
| language ID bn_in | PASS | detected=bn conf=0.77 alts=['as'], sim=0.96, wer=8.7%, cer=1.6% | 0.000 |
| language ID as_in | PASS | detected=as conf=0.77 alts=['bn'], sim=0.99, wer=4.8%, cer=1.7% | 0.000 |
| French audio (unsupported language) | INFO | model returned lang=en, text starts: 'THE ACCIDENT HAD LIEU IN TERRAIN MOUNTAINEUS AND IT SEMBLED ' | 0.000 |
| format sample.mp3 | PASS | sim=0.97, wer=4.2%, cer=2.2% | 0.000 |
| format sample.ogg | PASS | sim=0.97, wer=4.2%, cer=2.2% | 0.000 |
| format sample.flac | PASS | sim=0.97, wer=4.2%, cer=2.2% | 0.000 |
| format sample.m4a | PASS | sim=0.97, wer=4.2%, cer=2.2% | 0.000 |
| format sample.aac | PASS | sim=0.97, wer=4.2%, cer=2.2% | 0.000 |
| format sample.webm | PASS | sim=0.97, wer=4.2%, cer=2.2% | 0.000 |
| format stereo_44k.wav | PASS | sim=0.97, wer=4.2%, cer=2.2% | 0.000 |
| format telephone_8k.wav | PASS | sim=0.94, wer=8.3%, cer=3.2% | 0.000 |
| format noisy.wav | PASS | sim=0.94, wer=12.5%, cer=3.2% | 0.000 |
| video te_speech.mp4 | PASS | lang=te, 8.28s | 0.000 |
| translate long English -> ta | PASS | chars 5209->6067, resplit=0, warnings=['a batch lost line breaks; retranslated line by line', 'a batch lost line breaks; retranslated line by line'] | 0.000 |
| translate Hindi text -> English | PASS | src=hi, chars 4796->4714 | 0.000 |
| speak ta (3 sentences) | PASS | 3 chunks, 20.76s, voice=Arun | 0.000 |
| back-transcribe the speech | PASS | similarity=0.98, wer=24.2%, cer=1.5% | 0.000 |
| OCR Hindi_1 | PASS | similarity=0.99, conf=0.844, wer=3.7%, cer=1.4% | 0.000 |
| OCR Tamil_1 | PASS | similarity=0.98, conf=0.655, wer=11.1%, cer=4.7% | 0.000 |
| OCR Kannada_1 | PASS | similarity=0.94, conf=0.781, wer=10.7%, cer=3.0% | 0.000 |
| OCR real iPhone photo (table notice) | PASS | 951 chars, table pipes=30, conf=0.704 | 0.000 |
| translate that table -> mr (markdown) | PASS | warnings=[] | 0.000 |
| OCR multi-script sign photo | PASS | 156 chars: '<!-- page 1 -->\nಮೈಸೂರು-ಮೈಲಾಡುತುರೈ ಎಕ್ಸ್\u200cಪ್ರೆಸ್\nमैसूरु-मयिलाडुतुरै ऐक्स्प्रेस\nமைசூரு-மயிலಾட' | 0.000 |
| OCR real table Hindi_table3 | PASS | table pipes=48, sim=0.70, wer=50.1%, cer=36.2% | 0.000 |
| OCR variant exif_rotated.jpg | PASS | sim vs Hindi_1 ground truth=0.99, wer=5.6%, cer=1.7% | 0.000 |
| OCR variant photo.heic | PASS | sim vs Hindi_1 ground truth=0.99, wer=3.7%, cer=1.4% | 0.000 |
| OCR variant rotated_90.png | PASS | sim vs Hindi_1 ground truth=0.99, wer=3.7%, cer=1.4% | 0.000 |
| OCR 4-page scanned PDF | PASS | pages 4/4, failed=[] | 0.000 |
| TOTAL spent by this run (ledger delta) | INFO | Rs 0.000 | 0.000 |
