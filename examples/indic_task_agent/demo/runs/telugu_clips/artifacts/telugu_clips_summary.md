# Telugu clip check and translations

## Which clips are Telugu

| Clip | Detected language | Telugu? |
|------|-------------------|---------|
| clip1.wav | Telugu (te) | Yes |
| clip2.wav | Hindi (Devanagari script; hi/mr/ne candidates) | No |
| clip3.wav | Telugu (te) | Yes |
| clip4.wav | Tamil (ta) | No |
| clip5.wav | Bengali (bn/as candidates) | No |

Telugu clips: **clip1.wav** and **clip3.wav**.

## Translations into English

**clip1.wav**

> The library will be open until eight o'clock this week because of exams.

**clip3.wav**

> My team won by twenty runs in the inter-school cricket match on Saturday.

## Notes

- Language identification for clip2 and clip5 returned several candidates because Devanagari and Bengali scripts are shared by multiple languages; neither is Telugu, so they were not translated.
- Translations were produced by the translation tool; checks (structure, numbers, length) passed for both.
- The Telugu transcripts are in the per-clip translation files and in the transcription tool output.
