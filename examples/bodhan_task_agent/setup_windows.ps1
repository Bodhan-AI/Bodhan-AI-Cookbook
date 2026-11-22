# Windows PowerShell helper; never prints or requests API secrets.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# The offline suite includes real audio conversion via ffmpeg.
# Fail clearly instead of reporting a successful setup after failed tests.
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or
    -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: FFmpeg and ffprobe are required for the audio tests." -ForegroundColor Red
    Write-Host "Install: winget install --id Gyan.FFmpeg -e"
    Write-Host "Close and reopen VS Code, then verify: ffmpeg -version; ffprobe -version"
    exit 1
}
if (-not (Test-Path .venv)) { py -3 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "project dependency installation failed" }
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Offline tests failed: inspect the failures above" }
Write-Host "All offline tests passed. Configure .env locally before a live run." -ForegroundColor Green
