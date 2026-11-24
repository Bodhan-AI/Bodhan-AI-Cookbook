# Diff against baseline

Baseline: `outputs/report_demo/items.jsonl`

260 items in both runs (31 with a different translation); 0 only in baseline, 0 only in this run.

- **Regressions:** 21 new flags (21 major or critical); 11 items went from clean to flagged.
- **Improvements:** 16 flags resolved; 14 items went from flagged to clean.

## Where it moved

| Category | Baseline flags | Current flags | Δ |
|---|---|---|---|
| term | 11 | 18 | +7 |
| mistranslation | 19 | 15 | -4 |
| omission | 12 | 16 | +4 |
| addition | 13 | 10 | -3 |
| format | 20 | 22 | +2 |
| wrong_language | 12 | 10 | -2 |
| number | 13 | 14 | +1 |

| Language | Baseline flags | Current flags | Δ |
|---|---|---|---|
| bn | 46 | 48 | +2 |
| hi | 49 | 48 | -1 |
| ta | 53 | 57 | +4 |

## Regressions (new in this run)

- **ta · number (critical)** `num-01.ta`: Source number '3,000' appears as '3,700' in the translation. — flagged text “3,700”
- **bn · number (critical)** `num-08.bn`: Source number '45' appears as '৬৫' in the translation. — flagged text “৬৫”
- **bn · number (critical)** `num-06.bn`: Source number '300,000' appears as '০০০০০০' in the translation. — flagged text “০০০০০০”
- **ta · format (major)** `md-01.ta`: The translation omitted the markdown heading "## Parts of a plant" present in the source.
- **hi · omission (major)** `ent-07.hi`: The translation completely omits the first sentence of the source, which states that Python and JavaScript are popular programming languages. This loss changes the overall information conveyed.
- **hi · term (major)** `ent-07.hi`: The translation leaves out the mention of JavaScript, which is present in the source.
- **ta · omission (major)** `ent-07.ta`: The translation does not include the first sentence of the source, which states that Python and JavaScript are popular programming languages. This information is completely missing.
- **ta · term (major)** `md-06.ta`: The word "[…]" means Spinach, not Lentils as required by the source. — flagged text “கீரை”
- **ta · term (major)** `md-06.ta`: The word "[…]" means Iron, not Protein as required by the source. — flagged text “இரும்புச்சத்து”
- **bn · omission (major)** `pro-11.bn`: The translation completely omits the first sentence about a balanced diet, providing only the part about drinking water.
- **ta · term (major)** `ent-07.ta`: The translation omits the term 'JavaScript' mentioned in the source, resulting in a missing term.
- **bn · term (major)** `md-06.bn`: The translation omits the source term 'Rice' (and its nutrient) from the table, so the term is missing.
- **bn · term (major)** `md-06.bn`: The translation omits the source term 'Carbohydrate' (and its associated item), so the term is not rendered.
- **hi · term (major)** `md-07.hi`: The translation omits the first bullet "Turn off the tap while brushing." which is a content loss.
- **hi · omission (major)** `md-07.hi`: The first bullet point from the source is completely missing in the Hindi translation. — flagged text “बचाना

- टपकती”

## Improvements (gone in this run)

- **bn · number (critical)** `num-05.bn.inj-number`: Source number '3' appears as '৬' in the translation. — flagged text “৬”
- **bn · wrong_language (critical)** `pro-03.bn.inj-wrong_language`: The text reads as Assamese, not Bengali (function words / characters). — flagged text “হৃদযন্ত্ৰই গোটেই শৰীৰত তেজ পাম্প কৰে। নিয়মীয়া ব্যায়ামে ইয়াক শক্তিশালী আৰু সুস্থ কৰি ৰাখে।”
- **hi · wrong_language (critical)** `pro-10.hi.inj-wrong_language`: The text reads as Marathi, not Hindi (function words / characters). — flagged text “ज्वालामुखी तेथे तयार होतात जेथे उष्ण वितळलेले खडक पृष्ठभागातील भेगांमधून वर येतात. काही बेटे पूर्णपणे ज्वालामुखीच्या उद्रेकामुळे निर्माण झाली होती.”
- **bn · number (major)** `num-08.bn.inj-number`: Source number '32' appears as '৩৫' in the translation. — flagged text “৩৫”
- **hi · format (major)** `md-04.hi.inj-format`: Link URLs differ from the source. — flagged text “[पोर्टल](https://example.com/guidelines)”
- **hi · omission (major)** `ent-05.hi.inj-omission`: The translation omits the second sentence of the source, which states that the programmes have reduced polio in many countries.
- **ta · omission (major)** `ent-05.ta.inj-omission`: The translation omits the second sentence of the source, which states that their programmes have reduced polio in many countries.
- **ta · addition (major)** `pro-11.ta.inj-addition`: The translation adds a third sentence about using sunlight, water and carbon dioxide to release oxygen, which is not present in the source text. — flagged text “அவை சூரிய ஒளி, நீர் மற்றும் கார்பன் டை ஆக்சைடு ஆகியவற்றை பயன்படுத்தி, ஆக்சிஜனை காற்றில் வெளியிடுகின்றன”
- **bn · addition (major)** `num-01.bn.inj-addition`: The translation includes an extra sentence about DNA that is not present in the source text, adding unrelated information. — flagged text “ডিএনএ জীবন্ত বস্তুর বংশগত নির্দেশ বহন করে”
- **bn · omission (major)** `num-09.bn.inj-omission`: The translation does not include the instruction to mix sugar, salt, and water, which is a substantive part of the source text.
