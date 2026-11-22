# Result: budget_exhausted

**Task:** Translate this scanned chapter into Marathi and keep the headings

## Summary
The run stopped when the budget ran out.

## Budget exhausted
Spent Rs 0.40 of the Rs 0.45 cap. The run stopped cleanly at step 2: `ocr_document`: BUDGET EXHAUSTED: read 2 of 8 pages; pages [3, 4, 5, 6, 7, 8] not done. Raise the cost cap to finish.

**Done before stopping**
- step 2 `ocr_document` path=~/Desktop/imp/bodhan/bodhan_tool_server_agent/test_data/pdf/scanned_chapter_hi.pdf (Rs 0.4)

**Planned steps (not all completed)**
- OCR the input PDF to extract text in markdown format.
- Translate the extracted markdown text into Marathi, preserving the markdown formatting to keep headings.
- Save the translated Marathi text to a file.
- Provide the path to the translated Marathi text file.

Partial outputs are kept in `artifacts/` and marked as partial. Rerun with a higher `--max-cost-inr` to finish; results already paid for come from the cache.

## Artifacts
- [ocr_output.md](artifacts/ocr_output.md) partial/intermediate

### ocr_output.md
```
<!-- page 1 -->
☞ सोरबिथिं आरो बेनि बायदिसिना जेंनानि
सोमोन्दै सांग्रां जानो हागोन।
☞ सोरबिथिं आरो बेनि जेंनाफोरनि बागै
बायदिसिना रोखोमनि रोंगौथि आरो
मानसिनि मावनो गोनां बिबान आरो
बिफावथिनि सोमोन्दै बुजिनो हागोन।
☞ सोरबिथिंनि फारसे मावनां गोसो-आखु
(Responsible Attitude) फोजौनो
हागोन।
☞ सोरबिथिं सरैखा खालामनो थाखाय मोनदांथि
आरो बेसेननांफोरखौ बुजिनो हागोन।
☞ सोरबिथिङारि जेंनाफोरखौ फोजोबस्रांनो
थाखाय हारोंथि (Skill) फोरखौ सोलोंनो
हागोन।

<!-- page 2 -->
सभी लोगों, जैसे नायक-नायिका, पात्र, संगीत निर्देशक, कथाकार, संवाद लेखक,
कैमरामेन आदि को एक सूत्र में बाँध कर मुझे साकार रूप देता है। मुझे बनाने में
हज़ारों लोगों की मेहनत जुड़ी होती है। मुझ से उनकी जीविका चलती है। उनकी
मेहनत पर ही मेरा भविष्य निर्भर करता है।
```

## Verification
- ocr pages `ocr_output.md`: FAILED ['2 of 8 pages read']

## Notes from the tools
- BUDGET EXHAUSTED: read 2 of 8 pages; pages [3, 4, 5, 6, 7, 8] not done. Raise the cost cap to finish.

## Warnings
- verification failed for: work/artifacts/ocr_output.md
- stopped early: budget

## Cost and time
- Bodhan cost: Rs 0.4 (cap Rs 0.45); time 6.3s; steps 2