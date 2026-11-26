# Diff vs baseline

Baseline: `./report_baseline`. Items only in this run: 0; only in baseline: 0.

- Regressions (new flags): **11**
- Improvements (flags gone): **0**

## Flags by category

| category | baseline | now | change |
| --- | --- | --- | --- |
| addition | 5 | 5 | +0 |
| format | 7 | 7 | +0 |
| garbled | 4 | 4 | +0 |
| mistranslation | 0 | 8 | +8 |
| number | 6 | 6 | +0 |
| omission | 5 | 6 | +1 |
| repetition | 1 | 1 | +0 |
| speech_omission | 3 | 3 | +0 |
| term | 0 | 2 | +2 |
| wrong_language | 5 | 5 | +0 |

## Quality score by language (MQM-style, higher is better)

| language | baseline | now |
| --- | --- | --- |
| bn | 92.64 | 91.65 |
| hi | 91.34 | 89.11 |
| ta | 84.38 | 82.47 |

## Regressions: flags that appeared

- `s01-ta` (ta) **mistranslation** [major] — judge: The source phrase 'At higher altitude' was mistranslated as '[text]' (keeps), which changes the meaning of the sentence. [quote: 'வைக்கிறது', agreement 100%]
- `s07-hi` (hi) **mistranslation** [minor] — source words ['lies', 'between'] lost in back-translation | judge: The word for 'border' ([text]) is misspelled as '[text]' (missing the nasal/m sound). || judge: The word for 'border' ([text]) is misspelled as '[text]' (missing the nasal/m sound). [quote: 'सीा', agreement 100%]
- `s08-ta` (ta) **mistranslation** [major] — source words ['Ganga', 'before'] lost in back-translation | judge: The term 'Bay of Bengal' was mistranslated as 'Degree Bay' ([text] [text]) instead of 'Bengal Bay' ([text] [text]). || judge: The term 'Bay of Bengal' was mistranslated as 'Degree Bay' ([text] [text]) instead of 'Bengal Bay' ([text] 
- `s10-bn` (bn) **mistranslation** [minor] — judge: The word for seconds is misspelled as '[text]' instead of '[text]'. [quote: 'সেেন্ডের', agreement 100%]
- `s12-hi` (hi) **omission** [minor] — judge: The information about borrowing 3 books is entirely missing from the translation. [quote: '3 books', agreement 50%]
- `s12-ta` (ta) **term** [minor] — judge: There is a slight typo/misspelling in the Tamil word for opened ('[text]' instead of '[text]'), but the meaning is clear. [quote: 'திறக்ப்பட்டு', agreement 50%]
- `s14-bn` (bn) **mistranslation** [major] — source words ['speed'] lost in back-translation | judge: The word 'speed' ([text] [text]) was mistranslated as 'difference' ([text]). || judge: The word 'speed' ([text] [text]) was mistranslated as 'difference' ([text]). [quote: 'পার্থক্য', agreement 100%]
- `s21-hi` (hi) **mistranslation** [major] — judge: The unit 'm/s²' was mistranslated because 'm' (meter) was incorrectly translated as '[text]' (plants) instead of '[text]'. [quote: 'पौधे/सेकंड²', agreement 50%]
- `s21-hi` (hi) **term** [major] — judge: The scientific unit 'm/s²' (meters per second squared) has been mistranslated as '[text]/[text]²', where '[text]' means 'plants'. [quote: 'पौधे/सेकंड²', agreement 50%]
- `s23-hi` (hi) **mistranslation** [minor] — source words ['timetable'] lost in back-translation | judge: The feminine possessive '[text]' does not agree with the feminine noun '[text]-[text]' (it should be '[text] [text]'). || judge: The feminine possessive '[text]' does not agree with the feminine noun '[text]-[text]' (it should be '[text] [
- ... and 1 more

## Improvements: flags that disappeared

None.
