# Changes since the baseline

Baseline: `report-demo-baseline`.

16 new flags (regressions), 12 resolved, 58 persisting, 0 with a changed severity; 20 items have a different translation; 0 items only in the baseline, 0 only in this run.

## Flags per language and category

| language | category | before | after | change |
|---|---|---|---|---|
| bn | MQM | 101 | 110 | +9 |
| bn | mistranslation | 14 | 12 | -2 |
| bn | number | 0 | 1 | +1 |
| bn | omission | 6 | 7 | +1 |
| hi | MQM | 77 | 100 | +23 |
| hi | addition | 1 | 3 | +2 |
| hi | format | 1 | 3 | +2 |
| hi | mistranslation | 10 | 7 | -3 |
| hi | wrong_language | 0 | 1 | +1 |
| ta | MQM | 109 | 120 | +11 |
| ta | format | 0 | 2 | +2 |
| ta | mistranslation | 13 | 12 | -1 |
| ta | number | 3 | 4 | +1 |
| ta | term | 7 | 6 | -1 |
| ta | wrong_language | 0 | 1 | +1 |

## New flags (regressions) (16)

| item | lang | category | severity | words | why | translation change |
|---|---|---|---|---|---|---|
| in22-0086-bodhan | hi | wrong_language | critical | মিয়ান মিরের ক্যাম্পে যদিও সিপাহীদের সং… | script: text in BENGALI script where DEVANAGARI was expected | −“मियाँ मीर के शिविर में, हालाँकि सिपाही अंग्रेज़ सैनिकों से चार गुना अधिक थे, लेकिन अंग्रेज़ अधिकारियों को उनके बारे में कोई संदेह नहीं था जब तक कि मेरठ से खब… |
| in22-0886-bodhan | bn | omission | critical | ২০২২ সালের ২৮শে মার্চ কাস্তিয়োর সরকারে… | LLM judge (high confidence): The English source says protests were 'against Castillo's go… | −“বিরুদ্ধে” +“জন্য” |
| short-heart | ta | wrong_language | critical | हृदय एक मेहनती मुट्ठी के आकार की मांसपे… | script: text in DEVANAGARI script where TAMIL was expected | −“இதயம் மனிதர்களிலும் விலங்குகளிலும் காணப்படும் கடினமான, ஒரு கைப்பிடி அளவுள்ள தசை ஆகும்.” +“हृदय एक मेहनती मुट्ठी के आकार की मांसपेशी है जो लोगों और जानवरों मे… |
| ent-isro | hi | addition | major | जन्म के समय एक हाथी का बच्चा 100 किलो त… | LLM judge (high confidence): The translation includes an entire extra sentence about a ba… | +“जन्म के समय एक हाथी का बच्चा 100 किलो तक भारी हो सकता है।” |
| in22-0154-bodhan | hi | format | major | न्यूटन का दूसरा नियम कहता है कि $F = ma… | format: math: source has [], translation has ['$F = ma$'] | +“न्यूटन का दूसरा नियम कहता है कि $F = ma$।” |
| in22-0522-human | bn | term | major | দক্ষিণ ভারতে, সিংহবিষ্ণু (৫৭৫ খ্রিষ্টাব… | LLM judge (high confidence): The phrase describing that each territorially segmented peop… | −“ভারতে” +“ভারতে, সিংহবিষ্ণু (৫৭৫ খ্রিষ্টাব্দ-৯০০ খ্রিষ্টাব্দ) থেকে শুরু হওয়া চোল যুগ ছিল দক্ষিণ ভারতীয় সমাজের একটি রূপান্তরকারী পর্যায় যেখানে”; −“আলওয়ার” +… |
| in22-0566-bodhan | hi | omission | major | 1913 और 1929 के बीच, इसके कृषि आयात में… | LLM judge (high confidence): The English source says 'agricultural exports' but the Hindi… | −“निर्यात” +“आयात” |
| in22-0698-bodhan | bn | mistranslation | major | এই ঋতুভিত্তিক স্বতন্ত্র সম্প্রদায়গুলির… | LLM judge (high confidence): The phrase specifying the main climatic factors (temperature… | −“হয়, প্রধানত তাপমাত্রা এবং বৃষ্টিপাতের হার।” +“হয়।” |
| in22-0886-human | hi | number | major | कैस्टिलो की सरकार के खिलाफ 28 मार्च 602… | numbers: source says 2022, translation says 6022 | −“2022” +“6022” |
| in22-0939-human | hi | addition | major | क्रिकेट का खेल, जैसा कि आज खेला जाता है… | LLM judge (high confidence): The translation contains an entire segment (T2) about cricke… | +“क्रिकेट का खेल, जैसा कि आज खेला जाता है, इसकी उत्पत्ति इंग्लैंड के दक्षिण-पूर्वी भाग में हुई है।” |
| in22-1009-human | hi | mistranslation | major | केवल | adjudicator: The translation uses 'केवल' (only) instead of 'भी' (also), reversing the mea… | +“केवल”; −“हैं,” +“हैं”; −“शिलांग” +“कुछ व्यूप्वाइंट्स और शिलॉन्ग”; −“(एलिफेंट जलप्रपात, मॉकडॉक डिम्पेप वैली व्यू और अन्य स्थल) पड़ने वाले कुछ दार्शनिक” +“स्थि… |
| num-railways | bn | number | major | ৬৭,৯৭৬ | numbers: source says 67956, translation says 67976 | −“৬৭,৯৫৬” +“৬৭,৯৭৬” |
| num-un | ta | number | major | இப்போது 193 பேர் உள்ளனர். | numbers: number missing in translation | −“அது நிறுவப்பட்டபோது அதில் 51 உறுப்பினர்கள் இருந்தனர்.” |
| tex-kinetic | ta | format | major | ஒரு நகரும் பொருளின் இயக்க ஆற்றல் $E_k =… | format: math: source has ['$E_k = \\frac{1}{2}mv^2$'], translation has [] | −“\frac{1}{2}mv^2$” +“\frac{1}{2}mv^2” |
| md-data | ta | format | minor | தரவு கோப்புகளைப் படித்தல் ஒரு அட்டவணையை… | format: heading: source has [2], translation has [] | −“##” |
| tex-circle | hi | format | minor | वृत्त का क्षेत्रफल त्रिज्या $r$ वाले वृ… | format: heading: source has [2], translation has [] | −“##” |

## Resolved (12)

| item | lang | category | severity | words | why | translation change |
|---|---|---|---|---|---|---|
| in22-0086-bodhan | hi | number | major | मियाँ मीर के शिविर में, हालाँकि सिपाही … | LLM judge (medium confidence): The English says "outnumbered ... by four to one" (a 4:1 r… | −“मियाँ मीर के शिविर में, हालाँकि सिपाही अंग्रेज़ सैनिकों से चार गुना अधिक थे, लेकिन अंग्रेज़ अधिकारियों को उनके बारे में कोई संदेह नहीं था जब तक कि मेरठ से खब… |
| in22-0698-bodhan | bn | mistranslation | major | স্বতন্ত্র | adjudicator: 'স্বতন্ত্র' means independent/isolated, not distinctive; changes meaning of … | −“হয়, প্রধানত তাপমাত্রা এবং বৃষ্টিপাতের হার।” +“হয়।” |
| in22-0698-bodhan | bn | mistranslation | major | জীবনধারা | adjudicator: 'জীবনধারা' means lifestyles, not biological life forms; completely different… | −“হয়, প্রধানত তাপমাত্রা এবং বৃষ্টিপাতের হার।” +“হয়।” |
| in22-0886-bodhan | bn | term | major | কাস্তিয়োর | LLM judge (high confidence): The English source says "Castillo's government" but the Beng… | −“বিরুদ্ধে” +“জন্য” |
| in22-0886-human | hi | mistranslation | major | कैस्टिलो की सरकार के खिलाफ 28 मार्च 202… | LLM judge (high confidence): The English source says 'Mass protests' (plural), indicating… | −“2022” +“6022” |
| in22-1009-human | hi | mistranslation | major | स्थानीय टैक्सियाँ चेरापूंजी तक जाती हैं… | LLM judge (high confidence): The Hindi word 'दार्शनिक' means 'philosophical', but the Eng… | +“केवल”; −“हैं,” +“हैं”; −“शिलांग” +“कुछ व्यूप्वाइंट्स और शिलॉन्ग”; −“(एलिफेंट जलप्रपात, मॉकडॉक डिम्पेप वैली व्यू और अन्य स्थल) पड़ने वाले कुछ दार्शनिक” +“स्थि… |
| num-un | ta | term | major | 193 பேர் | adjudicator: Translation uses 'பேர்' (people/persons) for member states; should use […] ந… | −“அது நிறுவப்பட்டபோது அதில் 51 உறுப்பினர்கள் இருந்தனர்.” |
| short-heart | ta | mistranslation | major | கடினமான | adjudicator: English 'hard-working' means diligent/continuously active (functional), but … | −“இதயம் மனிதர்களிலும் விலங்குகளிலும் காணப்படும் கடினமான, ஒரு கைப்பிடி அளவுள்ள தசை ஆகும்.” +“हृदय एक मेहनती मुट्ठी के आकार की मांसपेशी है जो लोगों और जानवरों मे… |
| in22-0698-bodhan | bn | mistranslation | minor | জলবায়ুর ঋতু | adjudicator: 'জলবায়ুর ঋতু' means 'season of climate', losing sense of seasonal pattern/c… | −“হয়, প্রধানত তাপমাত্রা এবং বৃষ্টিপাতের হার।” +“হয়।” |
| in22-0854-human | hi | mistranslation | minor | उदाहरण के तौर पर, "किसी व्यक्ति को तब र… | LLM judge (high confidence): The English source uses the feminine pronoun "she" ("if she … | −“तौर पर, "किसी” +“लिए, "एक”; −“तब”; −“जब” +“यदि”; −“एक”; −“राज्य” +“देश”; −“हुआ हो जहाँ नागरिकता का आधार रक्त से माना जाता है,” +“होता है जो जस् सैंग्विनिस ला… |
| in22-0939-human | hi | mistranslation | minor | इनुइत लोगों की पारंपरिक कहानियाँ, रिवाज… | LLM judge (high confidence): The English word 'rituals' refers to specific ceremonial or … | +“क्रिकेट का खेल, जैसा कि आज खेला जाता है, इसकी उत्पत्ति इंग्लैंड के दक्षिण-पूर्वी भाग में हुई है।” |
| in22-1009-human | hi | omission | minor | also | adjudicator: The word 'also' indicating taxis are an additional travel option is not tran… | +“केवल”; −“हैं,” +“हैं”; −“शिलांग” +“कुछ व्यूप्वाइंट्स और शिलॉन्ग”; −“(एलिफेंट जलप्रपात, मॉकडॉक डिम्पेप वैली व्यू और अन्य स्थल) पड़ने वाले कुछ दार्शनिक” +“स्थि… |

## Severity changed (0)

None.

Persisting flags: 58 (same category, place and severity).
