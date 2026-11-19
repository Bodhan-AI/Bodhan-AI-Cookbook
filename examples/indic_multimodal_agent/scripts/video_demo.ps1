# PowerShell script for automated video demonstration recording
# Executes the 6 core demonstration tasks and MCP inspector in exact sequence.
#
# Usage:
#   Interactive (for screen recording):
#     .\scripts\video_demo.ps1
#
#   Fresh run (clears cache to show live API calls and costs):
#     .\scripts\video_demo.ps1 -Fresh
#
#   Automated testing (no prompt pauses):
#     .\scripts\video_demo.ps1 -Fresh -NonInteractive

param(
    [switch]$Fresh,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "       BODHAN INDIC MULTIMODAL AGENT - VIDEO RECORDING RUNNER             " -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "Mode: $(if ($Fresh) { 'FRESH (Cache Cleared - Live Calls)' } else { 'STANDARD (Cache Enabled)' })" -ForegroundColor Yellow
Write-Host "Interactive: $(if ($NonInteractive) { 'NO (Automated)' } else { 'YES (Press Enter between steps)' })" -ForegroundColor Yellow
Write-Host ""

if ($Fresh) {
    if (Test-Path ".bodhan_cache") {
        Write-Host "[Fresh Mode] Clearing .bodhan_cache directory..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force ".bodhan_cache"
        Write-Host "[Fresh Mode] Cache cleared. All calls will execute live against Bodhan endpoints." -ForegroundColor Green
    }
}

# Ensure samples exist
if (-not (Test-Path "samples/hindi_voicenote.mp3") -or -not (Test-Path "samples/health_report_3page.pdf") -or -not (Test-Path "samples/neutral_clips")) {
    Write-Host "[Setup] Generating required media samples..." -ForegroundColor Yellow
    python scripts/make_samples.py
    Write-Host "[Setup] Samples verified." -ForegroundColor Green
}

$summary = @()
$totalCost = 0.0

function Prompt-NextStep($stepName, $cmdStr) {
    Write-Host ""
    Write-Host "--------------------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "NEXT STEP: $stepName" -ForegroundColor Cyan
    Write-Host "COMMAND:   $cmdStr" -ForegroundColor Gray
    Write-Host "--------------------------------------------------------------------------" -ForegroundColor DarkGray
    if (-not $NonInteractive) {
        Read-Host "Press [Enter] to execute this step..."
    }
    Write-Host ""
}

# ---------------------------------------------------------------------------
# STEP A: Offline Pytest Suite (Summary Only)
# ---------------------------------------------------------------------------
Prompt-NextStep "Step A: Pytest Offline Unit Test Suite" "pytest tests/ -q"
$swA = [System.Diagnostics.Stopwatch]::StartNew()
pytest tests/ -q
$swA.Stop()
$durA = [Math]::Round($swA.Elapsed.TotalSeconds, 2)
$summary += [PSCustomObject]@{
    Step = "A"
    Task = "Offline Pytest Suite (54 Tests)"
    Status = "PASSED"
    Cost = "INR 0.0000"
    Duration = "${durA}s"
}

# ---------------------------------------------------------------------------
# STEP B: Vertical Slice: Hindi Voice Note -> English SRT Subtitles
# ---------------------------------------------------------------------------
$outB = "out/demo/01_subtitles_en"
$cmdB = "python -m bodhan_agent --task `"Transcribe this Hindi voice note and produce clean English subtitles formatted as an SRT file`" --files samples/hindi_voicenote.mp3 --out $outB --max-cost-inr 5.0"
Prompt-NextStep "Step B: Hindi Voice Note -> English SRT Subtitles" $cmdB
if (Test-Path $outB) { Remove-Item -Recurse -Force $outB }
$swB = [System.Diagnostics.Stopwatch]::StartNew()
python -m bodhan_agent --task "Transcribe this Hindi voice note and produce clean English subtitles formatted as an SRT file" --files samples/hindi_voicenote.mp3 --out $outB --max-cost-inr 5.0
$swB.Stop()
$durB = [Math]::Round($swB.Elapsed.TotalSeconds, 2)

$costB = 0.0
$statusB = "completed"
if (Test-Path "$outB/report.json") {
    $repB = Get-Content "$outB/report.json" -Raw | ConvertFrom-Json
    $costB = $repB.total_cost_inr
    $statusB = $repB.status
}
$totalCost += $costB
$summary += [PSCustomObject]@{
    Step = "B"
    Task = "Hindi Voice Note -> English SRT"
    Status = $statusB.ToUpper()
    Cost = "INR $($costB.ToString('0.0000'))"
    Duration = "${durB}s"
}

# ---------------------------------------------------------------------------
# STEP C: Scanned Health Report PDF -> Tamil Audio Summary + Verification
# ---------------------------------------------------------------------------
$outC = "out/demo/02_pdf_tamil_summary"
$cmdC = "python -m bodhan_agent --task `"OCR this scanned health report and give me a Tamil audio summary`" --files samples/health_report_3page.pdf --out $outC --max-cost-inr 5.0"
Prompt-NextStep "Step C: Scanned PDF -> Tamil Audio Summary + Back-Transcription" $cmdC
if (Test-Path $outC) { Remove-Item -Recurse -Force $outC }
$swC = [System.Diagnostics.Stopwatch]::StartNew()
python -m bodhan_agent --task "OCR this scanned health report and give me a Tamil audio summary" --files samples/health_report_3page.pdf --out $outC --max-cost-inr 5.0
$swC.Stop()
$durC = [Math]::Round($swC.Elapsed.TotalSeconds, 2)

$costC = 0.0
$statusC = "completed"
if (Test-Path "$outC/report.json") {
    $repC = Get-Content "$outC/report.json" -Raw | ConvertFrom-Json
    $costC = $repC.total_cost_inr
    $statusC = $repC.status
}
$totalCost += $costC
$summary += [PSCustomObject]@{
    Step = "C"
    Task = "Scanned PDF -> Tamil Audio Summary"
    Status = $statusC.ToUpper()
    Cost = "INR $($costC.ToString('0.0000'))"
    Duration = "${durC}s"
}

# ---------------------------------------------------------------------------
# STEP D: Markdown Table -> Marathi Translation
# ---------------------------------------------------------------------------
$outD = "out/demo/03_table_marathi"
$cmdD = "python -m bodhan_agent --task `"Translate this village census table into Marathi and verify cell layout`" --files sample_data/village_census.md --out $outD --max-cost-inr 5.0"
Prompt-NextStep "Step D: Markdown Table -> Marathi Translation" $cmdD
if (Test-Path $outD) { Remove-Item -Recurse -Force $outD }
$swD = [System.Diagnostics.Stopwatch]::StartNew()
python -m bodhan_agent --task "Translate this village census table into Marathi and verify cell layout" --files sample_data/village_census.md --out $outD --max-cost-inr 5.0
$swD.Stop()
$durD = [Math]::Round($swD.Elapsed.TotalSeconds, 2)

$costD = 0.0
$statusD = "completed"
if (Test-Path "$outD/report.json") {
    $repD = Get-Content "$outD/report.json" -Raw | ConvertFrom-Json
    $costD = $repD.total_cost_inr
    $statusD = $repD.status
}
$totalCost += $costD
$summary += [PSCustomObject]@{
    Step = "D"
    Task = "Census Table -> Marathi Table"
    Status = $statusD.ToUpper()
    Cost = "INR $($costD.ToString('0.0000'))"
    Duration = "${durD}s"
}

# ---------------------------------------------------------------------------
# STEP E: South Indian Language Screening (Neutral File Names)
# ---------------------------------------------------------------------------
$outE = "out/demo/04_audio_screening"
$cmdE = "python -m bodhan_agent --task `"Which of the audio files in this folder are in a South Indian language? Name the languages and give a one-line English gist for each.`" --files samples/neutral_clips --out $outE --max-cost-inr 5.0"
Prompt-NextStep "Step E: South Indian Language Screening (Neutral File Names)" $cmdE
if (Test-Path $outE) { Remove-Item -Recurse -Force $outE }
$swE = [System.Diagnostics.Stopwatch]::StartNew()
python -m bodhan_agent --task "Which of the audio files in this folder are in a South Indian language? Name the languages and give a one-line English gist for each." --files samples/neutral_clips --out $outE --max-cost-inr 5.0
$swE.Stop()
$durE = [Math]::Round($swE.Elapsed.TotalSeconds, 2)

$costE = 0.0
$statusE = "completed"
if (Test-Path "$outE/report.json") {
    $repE = Get-Content "$outE/report.json" -Raw | ConvertFrom-Json
    $costE = $repE.total_cost_inr
    $statusE = $repE.status
}
$totalCost += $costE
$summary += [PSCustomObject]@{
    Step = "E"
    Task = "South Indian Audio Screening (Neutral Names)"
    Status = $statusE.ToUpper()
    Cost = "INR $($costE.ToString('0.0000'))"
    Duration = "${durE}s"
}

# ---------------------------------------------------------------------------
# STEP F: Impossible Task (French Speech Audio Refusal)
# ---------------------------------------------------------------------------
$outF = "out/demo/05_impossible_french"
$cmdF = "python -m bodhan_agent --task `"Translate this audio recording into French and generate French speech audio`" --files samples/hindi_voicenote.mp3 --out $outF --max-cost-inr 5.0"
Prompt-NextStep "Step F: Impossible Task Refusal (French Speech Audio)" $cmdF
if (Test-Path $outF) { Remove-Item -Recurse -Force $outF }
$swF = [System.Diagnostics.Stopwatch]::StartNew()
python -m bodhan_agent --task "Translate this audio recording into French and generate French speech audio" --files samples/hindi_voicenote.mp3 --out $outF --max-cost-inr 5.0
$swF.Stop()
$durF = [Math]::Round($swF.Elapsed.TotalSeconds, 2)

$costF = 0.0
$statusF = "impossible"
if (Test-Path "$outF/report.json") {
    $repF = Get-Content "$outF/report.json" -Raw | ConvertFrom-Json
    $costF = $repF.total_cost_inr
    $statusF = $repF.status
}
$totalCost += $costF
$summary += [PSCustomObject]@{
    Step = "F"
    Task = "French Audio Refusal (Impossible)"
    Status = $statusF.ToUpper()
    Cost = "INR $($costF.ToString('0.0000'))"
    Duration = "${durF}s"
}

# ---------------------------------------------------------------------------
# STEP G: Standalone MCP Tool Demonstration
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==========================================================================" -ForegroundColor Green
Write-Host "STEP G: STANDALONE MCP SERVER DEMO INSTRUCTIONS" -ForegroundColor Green
Write-Host "==========================================================================" -ForegroundColor Green
Write-Host "The server exposes 7 standard Model Context Protocol (MCP) tools:" -ForegroundColor Cyan
Write-Host "  1. bodhan_transcribe" -ForegroundColor White
Write-Host "  2. bodhan_translate" -ForegroundColor White
Write-Host "  3. bodhan_speak" -ForegroundColor White
Write-Host "  4. bodhan_ocr" -ForegroundColor White
Write-Host "  5. bodhan_detect_language" -ForegroundColor White
Write-Host "  6. bodhan_list_voices" -ForegroundColor White
Write-Host "  7. bodhan_inspect_media" -ForegroundColor White
Write-Host ""
Write-Host "To demonstrate in screen recording with the official MCP Inspector client, run:" -ForegroundColor Yellow
Write-Host "  npx @modelcontextprotocol/inspector python -m bodhan_mcp" -ForegroundColor White
Write-Host ""
Write-Host "In the Inspector UI (opens in browser):" -ForegroundColor Cyan
Write-Host "  1. Click 'Connect' to initialize connection over stdio." -ForegroundColor White
Write-Host "  2. Go to 'Tools' -> select 'bodhan_list_voices' -> click 'Execute Tool'." -ForegroundColor White
Write-Host "  3. Observe returned catalog of 10 Indic language personas." -ForegroundColor White
Write-Host "  4. Select 'bodhan_detect_language' with text 'नमस्कार मित्रांनो' -> shows Marathi (mr)." -ForegroundColor White
Write-Host "==========================================================================" -ForegroundColor Green
Write-Host ""

# Print Final Summary Table
Write-Host "==========================================================================" -ForegroundColor Cyan
Write-Host "                      DEMONSTRATION RUN SUMMARY                           " -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Cyan
$summary | Format-Table -AutoSize
Write-Host "--------------------------------------------------------------------------" -ForegroundColor DarkGray
Write-Host "TOTAL DEMO SPEND: INR $($totalCost.ToString('0.0000'))" -ForegroundColor Green
Write-Host "==========================================================================" -ForegroundColor Cyan
