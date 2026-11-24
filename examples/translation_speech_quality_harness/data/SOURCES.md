# Demo data sources

Every English source text in `sources_en.jsonl` (60 texts × hi, ta, bn = 180 items) and `heldout_sources_en.jsonl`
(10 texts each in te and mr, for the judge bake-off and the held-out-language checks) is taken from English Wikipedia,
licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The revision used is linked for each item.

**NCERT** chapters are only a topic guide: they decided *which* subjects and facts to cover (classes 6–12). No NCERT text is
used (NCERT books are all rights reserved; decision D40). No English was written by us: sentences are Wikipedia's.

**Changes made to the Wikipedia text** (CC BY-SA asks to indicate them): passages are selected sentences; some sentences are
trimmed (alternate names, pronunciations, unit conversions in brackets and footnote markers removed, a pronoun replaced by
the noun it refers to); lists, headings, bold text and tables are re-laid-out as Markdown; formulas are the article's own
LaTeX from its wikitext. `sources_meta.json` lists, per item, every sentence that is not word-for-word in the revision
("adapted"). 37 of 60 texts are word-for-word; the others differ only by those trims.

Mix: fact 21, latex 9, markdown 9, paragraph 15, table 6. Subjects: Biology 7, Chemistry 5, Economics 4, Geography 14, History 7, Mathematics 4, Physics 14, Political Science 4, Science 1.

| ID | Type | Subject | NCERT topic (guide) | Wikipedia article (revision) | Sentences adapted |
|---|---|---|---|---|---:|
| geo-01 | fact | Geography | Class 9 Geography (Contemporary India I): Physical Features of India | [Kangchenjunga](https://en.wikipedia.org/w/index.php?title=Kangchenjunga&oldid=1386745234) | 1 |
| geo-02 | fact | Geography | Class 9 Geography (Contemporary India I): Drainage | [Ganges](https://en.wikipedia.org/w/index.php?title=Ganges&oldid=1383946766) | 2 |
| geo-03 | fact | Geography | Class 9 Geography (Contemporary India I): India – Size and Location | [Geography of India](https://en.wikipedia.org/w/index.php?title=Geography+of+India&oldid=1385472360) | 2 |
| geo-04 | fact | Geography | Class 10 Geography (Contemporary India II): Water Resources | [Bhakra Dam](https://en.wikipedia.org/w/index.php?title=Bhakra+Dam&oldid=1383449831) | 0 |
| geo-05 | fact | Geography | Class 10 Geography (Contemporary India II): Agriculture | [Agriculture in India](https://en.wikipedia.org/w/index.php?title=Agriculture+in+India&oldid=1386855292) | 0 |
| geo-06 | fact | Geography | Class 9 Geography (Contemporary India I): Climate | [Climate of India](https://en.wikipedia.org/w/index.php?title=Climate+of+India&oldid=1385255101) | 0 |
| phy-01 | fact | Physics | Class 11 Physics: Units and Measurement | [Speed of light](https://en.wikipedia.org/w/index.php?title=Speed+of+light&oldid=1384718539) | 0 |
| phy-02 | fact | Physics | Class 9 Science: Gravitation | [Gravity of Earth](https://en.wikipedia.org/w/index.php?title=Gravity+of+Earth&oldid=1384882484) | 1 |
| phy-03 | fact | Physics | Class 9 Science: Sound | [Speed of sound](https://en.wikipedia.org/w/index.php?title=Speed+of+sound&oldid=1385686216) | 2 |
| chem-01 | fact | Chemistry | Class 9 Science: Matter in Our Surroundings | [Properties of water](https://en.wikipedia.org/w/index.php?title=Properties+of+water&oldid=1384697386) | 0 |
| chem-02 | fact | Chemistry | Class 9 Science: Structure of the Atom | [Electron](https://en.wikipedia.org/w/index.php?title=Electron&oldid=1386707997) | 1 |
| bio-01 | fact | Biology | Class 9 Science: The Fundamental Unit of Life | [Cell theory](https://en.wikipedia.org/w/index.php?title=Cell+theory&oldid=1384580439) | 0 |
| bio-02 | fact | Biology | Class 10 Science: Life Processes | [Heart](https://en.wikipedia.org/w/index.php?title=Heart&oldid=1385089655) | 0 |
| bio-03 | fact | Biology | Class 12 Biology: Human Health and Disease | [Malaria](https://en.wikipedia.org/w/index.php?title=Malaria&oldid=1387027699) | 0 |
| hist-01 | fact | History | Class 12 History (Themes in Indian History I): Bricks, Beads and Bones – The Harappan Civilisation | [Indus Valley Civilisation](https://en.wikipedia.org/w/index.php?title=Indus+Valley+Civilisation&oldid=1387089616) | 1 |
| hist-02 | fact | History | Class 10 History (India and the Contemporary World II): Nationalism in India | [Salt March](https://en.wikipedia.org/w/index.php?title=Salt+March&oldid=1386404353) | 2 |
| hist-03 | fact | History | Class 9 History (India and the Contemporary World I): The French Revolution | [Storming of the Bastille](https://en.wikipedia.org/w/index.php?title=Storming+of+the+Bastille&oldid=1383995988) | 1 |
| civ-01 | fact | Political Science | Class 9 Political Science (Democratic Politics I): Constitutional Design | [Constitution of India](https://en.wikipedia.org/w/index.php?title=Constitution+of+India&oldid=1387140012) | 0 |
| civ-02 | fact | Political Science | Class 9 Political Science (Democratic Politics I): Electoral Politics | [Election Commission of India](https://en.wikipedia.org/w/index.php?title=Election+Commission+of+India&oldid=1383559941) | 1 |
| eco-01 | fact | Economics | Class 10 Economics (Understanding Economic Development): Money and Credit | [Reserve Bank of India](https://en.wikipedia.org/w/index.php?title=Reserve+Bank+of+India&oldid=1384839949) | 0 |
| sci-01 | fact | Physics | Class 11 Physics: Gravitation (satellites) | [Chandrayaan-3](https://en.wikipedia.org/w/index.php?title=Chandrayaan-3&oldid=1384830209) | 1 |
| bio-04 | paragraph | Biology | Class 11 Biology: Photosynthesis in Higher Plants | [Photosynthesis](https://en.wikipedia.org/w/index.php?title=Photosynthesis&oldid=1385696147) | 1 |
| geo-07 | paragraph | Geography | Class 11 Geography (Fundamentals of Physical Geography): Water in the Atmosphere | [Water cycle](https://en.wikipedia.org/w/index.php?title=Water+cycle&oldid=1368240581) | 0 |
| phy-04 | paragraph | Physics | Class 10 Science: The Human Eye and the Colourful World | [Rainbow](https://en.wikipedia.org/w/index.php?title=Rainbow&oldid=1386219386) | 0 |
| chem-03 | paragraph | Chemistry | Class 10 Science: Acids, Bases and Salts | [PH](https://en.wikipedia.org/w/index.php?title=PH&oldid=1386393044) | 3 |
| phy-05 | paragraph | Physics | Class 10 Science: Magnetic Effects of Electric Current | [Electromagnet](https://en.wikipedia.org/w/index.php?title=Electromagnet&oldid=1382781306) | 0 |
| bio-05 | paragraph | Biology | Class 12 Biology: Molecular Basis of Inheritance | [DNA](https://en.wikipedia.org/w/index.php?title=DNA&oldid=1386029671) | 1 |
| geo-08 | paragraph | Geography | Class 10 Science: Our Environment (ozone layer) | [Ozone layer](https://en.wikipedia.org/w/index.php?title=Ozone+layer&oldid=1384696269) | 1 |
| geo-09 | paragraph | Geography | Class 11 Geography (Fundamentals of Physical Geography): Interior of the Earth | [Internal structure of Earth](https://en.wikipedia.org/w/index.php?title=Internal+structure+of+Earth&oldid=1386795840) | 0 |
| hist-04 | paragraph | History | Class 10 History (India and the Contemporary World II): Nationalism in India | [Non-cooperation movement (1919–1922)](https://en.wikipedia.org/w/index.php?title=Non-cooperation+movement+%281919%E2%80%931922%29&oldid=1385930236) | 0 |
| hist-05 | paragraph | History | Class 12 History (Themes in Indian History I): Kings, Farmers and Towns | [Ashoka](https://en.wikipedia.org/w/index.php?title=Ashoka&oldid=1386648500) | 1 |
| civ-03 | paragraph | Political Science | Class 10 Political Science (Democratic Politics II): Federalism | [Panchayati raj in India](https://en.wikipedia.org/w/index.php?title=Panchayati+raj+in+India&oldid=1386441937) | 1 |
| hist-06 | paragraph | History | Class 12 History (Themes in Indian History II): Kings and Chronicles – The Mughal Courts | [Taj Mahal](https://en.wikipedia.org/w/index.php?title=Taj+Mahal&oldid=1387133948) | 3 |
| geo-10 | paragraph | Geography | Class 10 Geography (Contemporary India II): Forest and Wildlife Resources | [Project Tiger](https://en.wikipedia.org/w/index.php?title=Project+Tiger&oldid=1382235329) | 0 |
| geo-11 | paragraph | Geography | Class 11 Geography (Fundamentals of Physical Geography): Solar Radiation, Heat Balance and Temperature | [Greenhouse effect](https://en.wikipedia.org/w/index.php?title=Greenhouse+effect&oldid=1380683019) | 0 |
| eco-02 | paragraph | Economics | Class 9 Economics: Food Security in India | [Green Revolution in India](https://en.wikipedia.org/w/index.php?title=Green+Revolution+in+India&oldid=1381722612) | 0 |
| civ-04 | markdown | Political Science | Class 9 Political Science (Democratic Politics I): Democratic Rights | [Fundamental rights in India](https://en.wikipedia.org/w/index.php?title=Fundamental+rights+in+India&oldid=1378941196) | 0 |
| geo-12 | markdown | Geography | Class 10 Geography (Contemporary India II): Minerals and Energy Resources | [Renewable energy in India](https://en.wikipedia.org/w/index.php?title=Renewable+energy+in+India&oldid=1386874904) | 0 |
| bio-06 | markdown | Biology | Class 9 Science: Tissues | [Tissue (biology)](https://en.wikipedia.org/w/index.php?title=Tissue+%28biology%29&oldid=1385632505) | 0 |
| phy-06 | markdown | Physics | Class 9 Science: Force and Laws of Motion | [Newton's laws of motion](https://en.wikipedia.org/w/index.php?title=Newton%27s+laws+of+motion&oldid=1383155631) | 1 |
| chem-04 | markdown | Chemistry | Class 9 Science: Is Matter Around Us Pure? | [Separation process](https://en.wikipedia.org/w/index.php?title=Separation+process&oldid=1370920766) | 0 |
| geo-13 | markdown | Geography | Class 11 Geography (Fundamentals of Physical Geography): Composition and Structure of Atmosphere | [Atmosphere of Earth](https://en.wikipedia.org/w/index.php?title=Atmosphere+of+Earth&oldid=1381726987) | 2 |
| eco-03 | markdown | Economics | Class 10 Economics (Understanding Economic Development): Sectors of the Indian Economy | [Economic sector](https://en.wikipedia.org/w/index.php?title=Economic+sector&oldid=1381461405) | 0 |
| eco-04 | markdown | Economics | Class 10 Economics: Consumer Rights | [Consumer Protection Act, 2019](https://en.wikipedia.org/w/index.php?title=Consumer+Protection+Act%2C+2019&oldid=1369200430) | 0 |
| sci-02 | markdown | Science | Class 6 Science (Curiosity): The Wonderful World of Science | [Scientific method](https://en.wikipedia.org/w/index.php?title=Scientific+method&oldid=1381348974) | 0 |
| phy-07 | table | Physics | Class 11 Physics: Units and Measurement | [International System of Units](https://en.wikipedia.org/w/index.php?title=International+System+of+Units&oldid=1386810462) | 1 |
| phy-08 | table | Physics | Class 11 Physics: Units and Measurement | [International System of Units](https://en.wikipedia.org/w/index.php?title=International+System+of+Units&oldid=1386810462) | 0 |
| geo-14 | table | Geography | Class 9 Geography (Contemporary India I): Drainage | [List of rivers of India](https://en.wikipedia.org/w/index.php?title=List+of+rivers+of+India&oldid=1386598881) | 0 |
| chem-05 | table | Chemistry | Class 11 Chemistry: Classification of Elements and Periodicity in Properties | [List of chemical elements](https://en.wikipedia.org/w/index.php?title=List+of+chemical+elements&oldid=1384916014) | 1 |
| hist-07 | table | History | Class 12 History (Themes in Indian History II): Kings and Chronicles – The Mughal Courts | [List of emperors of the Mughal Empire](https://en.wikipedia.org/w/index.php?title=List+of+emperors+of+the+Mughal+Empire&oldid=1387188323) | 0 |
| bio-07 | table | Biology | Class 6 Science (Curiosity): Mindful Eating – A Path to a Healthy Body | [Vitamin](https://en.wikipedia.org/w/index.php?title=Vitamin&oldid=1384878089) | 0 |
| phy-09 | latex | Physics | Class 10 Science: Electricity | [Ohm's law](https://en.wikipedia.org/w/index.php?title=Ohm%27s+law&oldid=1386918016) | 0 |
| phy-10 | latex | Physics | Class 9 Science: Motion | [Equations of motion](https://en.wikipedia.org/w/index.php?title=Equations+of+motion&oldid=1386956605) | 1 |
| phy-11 | latex | Physics | Class 9 Science: Work and Energy | [Kinetic energy](https://en.wikipedia.org/w/index.php?title=Kinetic+energy&oldid=1370969006) | 0 |
| math-01 | latex | Mathematics | Class 10 Mathematics: Quadratic Equations | [Quadratic formula](https://en.wikipedia.org/w/index.php?title=Quadratic+formula&oldid=1382357522) | 0 |
| math-02 | latex | Mathematics | Class 10 Mathematics: Areas Related to Circles | [Area of a circle](https://en.wikipedia.org/w/index.php?title=Area+of+a+circle&oldid=1381258236) | 0 |
| math-03 | latex | Mathematics | Class 10 Mathematics: Triangles | [Pythagorean theorem](https://en.wikipedia.org/w/index.php?title=Pythagorean+theorem&oldid=1386629982) | 0 |
| math-04 | latex | Mathematics | Class 10 Mathematics: Arithmetic Progressions | [Arithmetic progression](https://en.wikipedia.org/w/index.php?title=Arithmetic+progression&oldid=1381409546) | 0 |
| phy-12 | latex | Physics | Class 10 Science: Light – Reflection and Refraction | [Lens](https://en.wikipedia.org/w/index.php?title=Lens&oldid=1384860947) | 0 |
| phy-13 | latex | Physics | Class 9 Science: Gravitation | [Newton's law of universal gravitation](https://en.wikipedia.org/w/index.php?title=Newton%27s+law+of+universal+gravitation&oldid=1386488007) | 0 |

Held-out languages (same texts, by ID): mr: geo-01, chem-02, bio-03, hist-03, eco-01, phy-04, hist-05, civ-04, chem-05, math-03; te: geo-02, phy-03, hist-02, civ-01, bio-04, geo-10, eco-02, eco-03, phy-07, phy-09.

Translations, audio and injected variants made from these texts are adaptations of them and are shared under the
same licence; see `LICENSE-DATA.md`. Generated audio is not committed (it is linked from the README).
