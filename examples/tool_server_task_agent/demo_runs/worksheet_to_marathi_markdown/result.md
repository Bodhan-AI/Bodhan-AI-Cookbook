# Task result: SUCCESS

**Task:** Translate this worksheet into Marathi and keep the table

## Summary

Translated the worksheet photo into Marathi while preserving the table structure. OCR extracted the worksheet, then it was translated to Marathi and verified for coverage and table rows/columns.

## Assumptions

- Output should be a Markdown worksheet with the table kept (not an image).

## Artifacts

- [worksheet_marathi_markdown_with_table.md](artifacts/worksheet_marathi_markdown_with_table.md) (621 bytes)

  ```
  इयत्ता ५ वी - गणित वर्कशीट
  
  किंमत यादी पहा आणि खालील प्रश्नांची उत्तरे द्या.
  
  | वस्तू | संख्या | किंमत (रु.) |
  |---|---|---|
  | पेन्सिल | १२ | ६ |
  | नोटबुक | ५ | ४० |
  | इरेजर | ८ | ३ |
  | रूलर | २ | १५ |
  
  १. कोणत्या वस्तूची किंमत सर्वाधिक आहे? २. ५ नोटबुक्सची एकूण किंमत शोधा.
  ```

## Verification

| Output | Check | Passed | Detail |
|---|---|---|---|
| translated_4297b7811b.md -> worksheet_marathi_markdown_with_table.md | translation | yes | {"failed_checks": []} |

## Cost and time

- Bodhan cost: Rs 0.202 of Rs 20.00 cap
- Tool calls: 3 (cache hits: 0)
- Wall time: 42.6s

_Trace: `trace.jsonl` · Report: `report.json`. Any generated audio is AI-generated speech (Bodhan Indic-Speak)._
