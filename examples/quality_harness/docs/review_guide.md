# How to review a translation sheet

This guide is for anyone filling in a review sheet made with
`python -m bodhan_eval.review export`. Your answers become the labels used to measure how often
the harness is right or wrong, so honest answers matter more than fast ones. "I'm not sure" is a
fine answer.

## The one question you're answering

> **Does the translation say what the English says: all of it, nothing more, correctly?**

Everything else in this guide just helps you answer that, one piece at a time.

## The three texts, and how much to trust each

| Column | What it is | How to use it |
|---|---|---|
| `source` | The original English | **The truth.** The translation is judged against this. |
| `translation` | The Hindi (or other language) being checked | **What you judge.** Your verdicts are about this text only. |
| `back_translation` | A machine's translation of the Hindi back into English | **A hint, never the truth.** It shows what the machine *thinks* the Hindi says. It is often wrong. |

The harness never sees what you see: it can't read Hindi, so it compares the English with the
back-translation and complains when they differ. The difference may be:

- a real mistake in the Hindi (the harness is right), or
- the back-translation rewording or garbling a correct Hindi sentence (a false alarm).

Only you can tell which, by reading the Hindi itself.

## Steps for each item

1. **Read the English** (`source` on the `item` row).
2. **Read the Hindi** (`translation`). Ask the one question above. Note anything that's wrong.
3. **Look at each complaint below the item** (rows numbered 0, 1, 2…). For each, read `text` (the
   words it points at) and `why` (what the harness thinks is wrong), then check the Hindi
   yourself and write a `verdict`:
   - **`real`**: the Hindi really has this problem → also fill `severity`, and fix `category` if
     it's the wrong kind (see below).
   - **`false`**: the Hindi is fine; the harness was fooled (usually by the back-translation).
   - **`unsure`**: you can't decide. Better than guessing.
4. **Add what the harness missed.** For each mistake from step 2 that no complaint covers, insert
   a new row under the item: copy its `id` and `lang`, type `missed` in `row`, then fill
   `category`, `severity` and `note`. One mistake per row; three mistakes = three `missed` rows.
5. **Nothing wrong?** Add nothing. An item with no `missed` rows and no `real` verdicts means "I
   checked, it's clean": that's useful information, not a gap.

If two complaints point at the same mistake, mark both `real`. Don't also add it as `missed`.

## Categories: what kind of mistake

| Category | Use when | Example (English → Hindi) |
|---|---|---|
| `mistranslation` | The Hindi says something **different** from the English | "Do not give this to children" → "यह बच्चों को दें" ("give this to children") |
| `omission` | Part of the English is **left out** | "Check both tyres every week" → "दोनों टायर चेक करें" (no "every week") |
| `addition` | The Hindi says something the English **doesn't** | "Closed on Sunday" → "रविवार और सोमवार को बंद" ("Sunday and Monday") |
| `number` | A number, date, amount or unit is **wrong, missing or extra** | "575 CE" → "570 ई." |
| `term` | A name or technical term is wrong, or replaced by a vaguer word | "repo rate" → "ब्याज दर" ("interest rate") |
| `format` | Markdown, tables or math are broken (a heading mark lost, a table row missing) | "### Caring for a bicycle" → "साइकिल की देखभाल" (no `###`) |
| `wrong_language` | Text in the wrong language or script, or left in English when it should be translated | a whole sentence left in English |
| `fluency` | The meaning is right but the Hindi is **ungrammatical or unnatural** | "लड़के खेलता है" (should be "खेलते हैं") |
| `other` | None of the above; explain in `note` | |

**Tricky choices:**

- **Singular/plural:** a different meaning (one student vs several) → `mistranslation`. Same
  meaning but a grammar slip (wrong verb ending) → `fluency`.
- **A word left out that changes the meaning** (like "not") → `mistranslation`, because the
  reader now understands something different. A plain leftover (a detail just missing) →
  `omission`.
- **The right number in the wrong sentence** → `mistranslation`, not `number`.

**Not mistakes:**

- Rewording that keeps the meaning ("opens at 10 a.m." → "सुबह 10 बजे खुलता है").
- Natural Hindi word order, or splitting/joining sentences.
- Names written in Devanagari ("Meerut" → "मेरठ"), or numbers in Devanagari digits ("575" → "५७५").
- The same quantity written another way ("7 million" → "70 लाख").

## Severity: how bad

| Severity | Meaning | Examples |
|---|---|---|
| `critical` | Meaning reversed, dangerous, or the translation is unusable | "not" dropped from a medical warning; mostly the wrong language |
| `major` | A fact, number, name or a real part of the meaning is wrong or missing | 575 → 570; "exports" → "imports"; a whole clause dropped |
| `minor` | Small; a reader still gets the right meaning | one student vs students where it hardly matters; a grammar slip; a lost heading mark |

Quick test: would a reader of the Hindi **believe something false** or **miss something
important**? Yes → `major` (or `critical` if it could cause harm). No → `minor`.

## What to write in `note`

One short line in English: **what's wrong and what it should be**. The note is for a person
reading the labels later, so be specific.

- Good: "says imports, should be exports"
- Good: "one officer, English says officers"
- Not useful: "wrong", "bad translation"

For `false` verdicts a note is optional; if you write one, say why the Hindi is fine ("Hindi says
किनारे = riverbank; back-translation reworded it").

## Worked examples

### 1. A real mistake the harness caught (from the sheet)

- English: "…beginning with Simhavishnu (575 CE-900 CE)…"
- Hindi: "…सिंहविष्णु (570 ई.-900 ई.)…"
- Complaint: `numbers` / "source says 575, translation says 570"

| row | verdict | severity | category | note |
|---|---|---|---|---|
| 0 | `real` | `major` | `number` (already right) | 575 written as 570 |

### 2. A false alarm caused by the back-translation (made up)

- English: "He walked along the bank of the river."
- Hindi: "वह नदी के किनारे-किनारे चला।"
- Back-translation: "He walked along the edge of the river."
- Complaint: `back_translation_phrase` / "back-translation lacks 'bank'"

The Hindi says किनारे (bank/edge of a river): correct. Only the back-translation picked another
English word.

| row | verdict | severity | category | note |
|---|---|---|---|---|
| 0 | `false` | | | किनारे is right; BT reworded |

### 3. A mistake the harness missed (made up)

- English: "Do not give this medicine to children under 5."
- Hindi: "यह दवा 5 साल से कम उम्र के बच्चों को दें।"
- No complaints (the back-translation happened to restore "not").

The Hindi says "give", not "do not give": meaning reversed, on a medical warning.

A new row under the item:

| id | lang | row | category | severity | note |
|---|---|---|---|---|---|
| (the item's id) | hi | `missed` | `mistranslation` | `critical` | "not" missing: says give it to children under 5 |

If the same item also had a grammar slip, add another `missed` row the same way, with `category` =
`fluency`, `severity` = `minor`, and a note.

### 4. A real mistake with the wrong category (made up)

- English: "The teacher praised the students."
- Hindi: "शिक्षक ने छात्र की प्रशंसा की।" (one student)
- Complaint: `back_translation` / `omission` / "'students' → 'student'"

The problem is real, but nothing is left out: the meaning changed from many to one.

| row | verdict | severity | category | note |
|---|---|---|---|---|
| 0 | `real` | `minor` | change `omission` → `mistranslation` | one student, English says students |

### 5. A format complaint (from the sheet)

- English starts with "### Caring for a bicycle" (a heading).
- Hindi starts with "साइकिल की देखभाल" with no `###`.
- Complaint: `format` / "heading: source has [3], translation has []"

The words are right but the heading mark is gone, so the text would show as plain text instead of
a heading.

| row | verdict | severity | category | note |
|---|---|---|---|---|
| 0 | `real` | `minor` | `format` | `###` heading mark dropped |

## Columns: what you fill in

| Row type (`row` column) | `verdict` | `severity` | `category` | `note` |
|---|---|---|---|---|
| A number (0, 1, 2…): the harness's complaint | **always** | if `real` | change only if wrong | optional |
| `item`: the texts | leave empty | leave empty | leave empty | leave empty |
| `missed`: a row you add | leave empty | **always** | **always** | **always** |

Don't change `id`, `lang`, the complaint numbers, or the `check`/`text`/`why` columns, and don't
sort the sheet. If you use Google Sheets, `false` turning into `FALSE` is fine.

When you're done, save as CSV (Google Sheets: File → Download → Comma-separated values). The
import checks every row and lists any it can't read, with its row number, so you can fix them.
