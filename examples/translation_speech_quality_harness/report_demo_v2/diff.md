# Diff against baseline

Baseline: `report_demo`

Compared **120** items (only in baseline: 0, only in this run: 0).
**49** items regressed (51 new flags; 44 items with a new major or critical one).
**51** items improved (52 flags gone).

| Language | weighted / 1k words, baseline | this run |
| --- | --- | --- |
| bn | 89.6 | 70.3 |
| hi | 78.1 | 91.8 |
| ta | 84.6 | 73.1 |

| Category | new | fixed |
| --- | --- | --- |
| addition | 6 | 4 |
| format | 7 | 9 |
| garbled | 4 | 5 |
| mistranslation | 6 | 8 |
| number | 6 | 5 |
| omission | 7 | 7 |
| speech_omission | 5 | 5 |
| speech_repetition | 0 | 1 |
| term | 5 | 3 |
| wrong_language | 5 | 5 |

Status changes: clean → flagged: 18, flagged → clean: 14, flagged → review: 4.

## Regressions

- **wmt-103 / hi**: addition, critical, confidence 0.99, found by back_translation, judge, length, numbers, regen_diff
  - source: “FTSE 250 firm famous for sausage rolls and steak bakes plans to rise branch network but not prices.”
  - translation: “दिल्ली मेट्रो प्रतिदिन लगभग 65 लाख यात्रियों को 393 किमी ट्रैक पर ले जाती है और इसकी पहली लाइन 24 दिसंबर 2002 को खुली थ…”
- **health-dose / bn**: mistranslation, critical, confidence 0.94, found by judge, regen_diff
  - source: “never more than 4 g in one day”
  - translation: “৪ গ্রামের কম গ্রহণ করবেন না”
- **wmt-95 / hi**: mistranslation, critical, confidence 0.94, found by judge, regen_diff
  - source: “Of course, if there was an obvious simple solution to a problem somebody with more knowledge and understanding of the p…”
  - translation: “कम ज्ञान और समझ रखने वाला व्यक्ति पहले ही उसे लागू कर चुका होता”
- **wmt-28 / hi**: omission, critical, confidence 0.85, found by judge
  - source: “and not beer”
- **ent-bank / hi**: number, major, confidence 0.99, found by judge, numbers, regen_diff
  - source: “4.5”
  - translation: “4.5”
- **geo-rivers / ta**: omission, major, confidence 0.99, found by back_translation, judge, regen_diff
  - source: “The Narmada is one of the few large rivers that flows west.”
  - translation: “| நர்மதா | 1,312 | அரபிக்கடல் |”
- **math-area / hi**: number, major, confidence 0.99, found by judge, numbers, regen_diff
  - source: “7”
  - translation: “9”
- **math-area / ta**: omission, major, confidence 0.99, found by back_translation, judge, regen_diff
  - source: “If the radius is doubled, the area becomes four times larger.”
  - translation: “உதாரணமாக, 7 செ.மீ ஆரம் கொண்ட ஒரு வட்டத்தின் பரப்பளவு சுமார் 154 செ.மீ² ஆகும்.”

## Improvements

- **wmt-28 / bn**: omission, critical, confidence 0.99, found by back_translation, judge, regen_diff
  - source: “and not beer”
  - translation: “তবে ঋষি সুনাকের দৃষ্টিভঙ্গিতে সমস্যা রয়েছে।”
- **wmt-28 / hi**: wrong_language, critical, confidence 0.99, found by judge, regen_diff, script
  - source: “European brewers have warned that exempting wine and not beer from deposit return schemes and reuse targets is unfair a…”
  - translation: “ஐரோப்பிய மதுபான உற்பத்தியாளர்கள், வைன் மற்றும் பீர் ஆகியவற்றை டெபாசிட் திரும்பப் பெறும் திட்டங்கள் மற்றும் மறுபயன்பாட்ட…”
- **sci-boiling / ta**: mistranslation, critical, confidence 0.94, found by judge, regen_diff
  - source: “boils”
  - translation: “உறைந்துவிடுகிறது”
- **wmt-54 / bn**: mistranslation, critical, confidence 0.94, found by judge, regen_diff
  - source: “keep the government open”
  - translation: “সরকার বন্ধ রাখার”
- **wmt-95 / bn**: mistranslation, critical, confidence 0.94, found by judge, regen_diff
  - source: “somebody with more knowledge and understanding of the problems”
  - translation: “কম জ্ঞান ও সমস্যা সম্পর্কে কম ধারণা আছে এমন কেউ”
- **wmt-95 / ta**: mistranslation, critical, confidence 0.94, found by judge, regen_diff
  - source: “more knowledge and understanding of the problems”
  - translation: “குறைவான அறிவும், பிரச்சனைகளைப் பற்றிய புரிதலும்”
- **civics-election / bn**: number, major, confidence 0.99, found by judge, numbers, regen_diff
  - source: “910”
  - translation: “৯৩”
- **civics-election / hi**: wrong_language, major, confidence 0.99, found by judge, regen_diff, script
  - source: “The Election Commission of India conducts elections to the Lok Sabha every five years.”
  - translation: “The Election Commission of India conducts elections to the Lok Sabha every five years”
