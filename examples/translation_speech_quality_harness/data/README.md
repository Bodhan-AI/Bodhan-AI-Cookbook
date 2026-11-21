# Data sources

## Demo fixture

The small demo sources and Indic translations in `demo.jsonl` are original assignment fixtures created for this harness.

## IN22 evaluation inputs

The `build_demo.py` script imports rows from the Hugging Face dataset-server `default/test` configuration. For IN22-Gen, columns are language codes such as `eng_Latn`, `hin_Deva`, `tam_Taml`, and `ben_Beng`; the builder maps those to the harness's English source and selected language targets. The paired-language `eng_Latn-hin_Deva` configuration described in the card is not exposed by the dataset-server API, and requesting it returns 404. Although IN22-Conv's dataset card describes paired configurations, its dataset-server API currently returns 404 for both splits and rows, so that variant is not currently downloadable with this script. Both dataset cards declare [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Cite [AI4Bharat's IndicTrans2 work](https://openreview.net/forum?id=vfT4YuzAYA) when publishing results; retain the dataset name and license fields emitted in each JSONL row, and include attribution and a license link if redistributing adapted data.

The Hugging Face dataset cards require a signed-in user to accept access terms. The fetch script reads `HF_TOKEN` and `HF_DATASETS_SERVER_URL` from the repository-root `.env` (without overriding values already set in PowerShell) or the process environment; it does not print or store credentials, install tools, call Bodhan, or modify `demo.jsonl`.

The imported target text is placed in both `translation` and `reference`, making these clean reference rows. This is **not** a Bodhan model output or a blind test: do not report the resulting reference-to-reference score as translation-model quality. Generated IN22 JSONL files are ignored by Git and should remain local evaluation inputs.

From the repository root, sign in at Hugging Face and accept the terms on each dataset card you plan to use. Create a read token at [Hugging Face Access Tokens](https://huggingface.co/settings/tokens), then run:

```powershell
$env:PYTHONUTF8 = "1"
$env:HF_DATASETS_SERVER_URL = "https://datasets-server.huggingface.co"
python examples\translation_speech_quality_harness\scripts\build_demo.py `
    --source in22-gen --langs hi,ta,bn --limit-per-language 100 --dry-run
$secureToken = Read-Host "Hugging Face read token" -AsSecureString
$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $env:HF_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    python examples\translation_speech_quality_harness\scripts\build_demo.py `
        --source in22-gen --langs hi,ta,bn --limit-per-language 100
}
finally {
    Remove-Item Env:HF_TOKEN -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    $secureToken.Dispose()
}
```

By default this writes `examples\translation_speech_quality_harness\data\in22_gen.jsonl`; it will refuse to replace an existing file. Use `--dry-run` first, `--out` for another destination, and `--force` only when intentionally replacing an output. Dataset downloads do not incur Bodhan model charges. The script will report a clear availability error if Hugging Face does not expose a selected dataset through its dataset-server API.
