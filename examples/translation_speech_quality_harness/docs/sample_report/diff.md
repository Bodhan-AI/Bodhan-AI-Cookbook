# Diff against baseline

Baseline: `outputs/demo_eval`

| Measure | Baseline | Current | Change |
|---|---|---|---|
| Quality score | 89.9 | 87.2 | -2.71 (worse) |
| Score: bn | 88.0 | 86.4 | -1.66 |
| Score: hi | 91.0 | 88.2 | -2.75 |
| Score: ta | 90.5 | 86.9 | -3.63 |

Flags: **30 new**, **0 fixed**, 0 changed, 53 unchanged.

## New flags

- `demo-bn-md-020`: **mistranslation** (major, 0.60): Token [35] says 'latitude' (…) for the E coordinate, which should be longitude (…), so the coordinate meaning is changed. (source: “32.348126°E”)
  - at: …১°দক্ষিণ ৩২.৩৪৮১২৬°পূর্ব **অক্ষাংশে** অবতরণ করে যা ম্যানজিনাস …
- `demo-bn-para-005`: **omission** (minor, 0.45): The word 'recorded' from 'fastest recorded delivery' has no counterpart in the translation after token [2]. (source: “fastest recorded delivery”)
  - at: ক্রিকেট বলের **দ্রুততম** ডেলিভারির জন্য সাধারণভাব…
- `demo-bn-para-014__omission`: **omission** (minor, 0.45): The phrase meaning 'even during the Games' is missing from the translation after token [4]. (source: “even during the Games”)
  - at: … গেমস বিশ্বজুড়ে স্থায়ী **শান্তি** আনেনি। যুদ্ধের কারণে তিন…
- `demo-bn-short-026`: **omission** (minor, 0.45): The adverb 'also' has no counterpart before the location verb at token [12]. (source: “also”)
  - at: …ের তৃতীয় উচ্চতম পর্বত যা **হিমালয়** রেঞ্জেই অবস্থিত।
- `demo-bn-short-032`: **speech_omission** (major, 0.65): 6 words are missing from the transcript (“পূর্বে যা মাদ্রাজ নামে পরিচিত ছিল”)
  - at: চেন্নাই (**পূর্বে যা মাদ্রাজ নামে পরিচিত ছিল**) হল ভারতের তামিলনাড়ু রা…
- `demo-bn-short-035__mistranslation`: **mistranslation** (major, 0.60): Tokens [13]-[15] negate the statement, saying the crust is not made of tectonic plates, which reverses the source meaning. (source: “it is made of big pieces called tectonic plates”)
  - at: …নিক প্লেট নামক বড় টুকরা **দিয়ে তৈরি নয়** যা ধীরে ধীরে চারপাশে সরে…
- `demo-hi-md-006__mistranslation`: **mistranslation** (major, 0.60): Token [7] negates the statement, so the translation says the equation is not true, reversing the source's meaning. (source: “is $E = mc^2$”)
  - at: …रसिद्ध समीकरण $E = mc^2$ **नहीं** है, जहाँ $c$ प्रकाश की ग…
- `demo-hi-md-012`: **speech_omission** (major, 0.70): audio stops early: the last 7 words are not in the transcript (“जैसे कई औद्योगिक शहर बसे हुए हैं”)
  - at: …बाद, बहरामपुर और कोलकाता **जैसे कई औद्योगिक शहर बसे हुए हैं**।
- `demo-hi-md-015__addition`: **mistranslation** (major, 0.60): Token [23] says width of the Moon's Earth-relative size as … applied to Earth, but the source sentence is about the Moon's width relative to Earth; the phrase '… … …' is acceptable, yet token [20] … with … reads as the Moon being a quarter of Earth's width which matches; no error here. (source: “The Moon is about one-fourth of the width of Earth.”)
  - at: … है। ⏎ - चंद्रमा पृथ्वी की **चौड़ाई का लगभग एक चौथाई है**। ⏎ - क्योंकि यह बहुत दूर ह…
- `demo-hi-md-018__term`: **mistranslation** (major, 0.60): Tokens [27]-[30] say the player is from New Ireland, which adds an unsourced place and replaces the generic 'each player'. (source: “Each player starts with 16 pieces”)
  - at: … में आठ वर्ग होते हैं। ⏎ - **न्यू आयरलैंड का खिलाड़ी** 16 मोहरों के साथ शुरुआत …
- `demo-hi-para-024__term`: **mistranslation** (major, 0.60): Tokens [2]-[4] say the Indus Water Treaty is endangered, replacing the source's Olympic Games, which is lost entirely. (source: “threatened the Olympic Games”)
  - at: आतंकवाद ने **सिंधु जल संधि** को भी खतरे में डाल दिया …
- `demo-hi-para-033__mistranslation`: **mistranslation** (major, 0.60): Tokens [6] and [7] add a negation, so the translation says the capital is not Sri Jayawardenepura Kotte, reversing the source meaning. (source: “The capital of the country is Sri Jayawardenepura Kotte”)
  - at: …श्री जयवर्धनेपुरा कोट्टे **नहीं है** जबकि सबसे बड़ा शहर कोलंब…
- `demo-hi-short-006`: **mistranslation** (minor, 0.45): The relative clause "which were slower" should modify the other areas (tokens [16]-[17]), but token [29]-[31] attaches it after the wages verb, so it reads as describing the wages. (source: “other areas which were slower”)
  - at: …(122% अधिक) और उच्च वेतन **मिला जो धीमे थे**।
- `demo-hi-short-009__repeat`: **mistranslation** (minor, 0.45): Token [12] … means layers rather than polar caps, though the back-translation shows the same drift. (source: “caps”)
  - at: … और कार्बन डाइऑक्साइड की **परतें** हैं।
- `demo-ta-md-001__wrong_language`: **wrong_language** (major, 0.85): sentence left untranslated in English: “…| Rajdhani Express | New Delhi | Mumbai | 16:55 |…”
  - at: …ேரம் \| ⏎ \|---\|---\|---\|---\| ⏎ **\| Rajdhani Express \| New Delhi \| Mumbai \| 16:55 \|** ⏎ \| துரந்தோ எக்ஸ்பிரஸ் \| க…
- … and 15 more
