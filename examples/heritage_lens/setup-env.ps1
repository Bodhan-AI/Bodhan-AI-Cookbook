<#
  Creates backend\.env from backend\.env.example and lets you type your Bodhan keys privately.
  - Keys are read with a hidden prompt, written only to backend\.env (which .gitignore excludes),
    and never printed.
  - Press Enter to skip any prompt.
  Run from the repository root:   .\setup-env.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$example = Join-Path $root "backend\.env.example"
$target  = Join-Path $root "backend\.env"
if (-not (Test-Path $example)) { throw "Cannot find $example. Run this script from the repository root." }
if (-not (Test-Path $target)) { Copy-Item $example $target; Write-Host "Created backend\.env from the example." }
else { Write-Host "backend\.env already exists; updating it in place." }

function Set-EnvValue([string]$Name, [string]$Value) {
  $lines = [System.Collections.Generic.List[string]]::new()
  $lines.AddRange([string[]](Get-Content -LiteralPath $target -Encoding UTF8))
  $found = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "^\s*$([regex]::Escape($Name))\s*=") { $lines[$i] = "$Name=$Value"; $found = $true }
  }
  if (-not $found) { $lines.Add("$Name=$Value") }
  # UTF-8 *without* BOM so the first line is parsed correctly
  [System.IO.File]::WriteAllLines($target, $lines, (New-Object System.Text.UTF8Encoding $false))
}
function Read-Secret([string]$Prompt) {
  $s = Read-Host -Prompt $Prompt -AsSecureString
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

# SECRET_KEY: generate a strong random value if it is still the placeholder
$current = (Select-String -Path $target -Pattern "^SECRET_KEY=(.*)$").Matches.Groups[1].Value
if ($current -like "replace_with*" -or [string]::IsNullOrWhiteSpace($current)) {
  $bytes = New-Object byte[] 48
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  Set-EnvValue "SECRET_KEY" ([Convert]::ToBase64String($bytes).Replace("+","-").Replace("/","_").TrimEnd("="))
  Write-Host "Generated a new SECRET_KEY."
}

Write-Host "`nBodhan keys (input is hidden; press Enter to skip). Use your NEWLY ROTATED keys."
$shared = Read-Secret "One key for all five services (or Enter to enter them separately)"
if ($shared) { Set-EnvValue "BODHAN_API_KEY" $shared }
$map = [ordered]@{
  "BODHAN_TRANSCRIBE_API_KEY"    = "indic-transcribe key"
  "BODHAN_TRANSLATE_API_KEY"     = "indic-translate key"
  "BODHAN_TRANSLITERATE_API_KEY" = "indic-transliterate key"
  "BODHAN_OCR_API_KEY"           = "indic-ocr key"
  "BODHAN_SPEAK_API_KEY"         = "indic-speak key"
}
foreach ($name in $map.Keys) {
  $v = Read-Secret $map[$name]
  if ($v) { Set-EnvValue $name $v }
}
Write-Host "`nDone. Keys were written to backend\.env only. Restart the backend, then check:"
Write-Host "  http://127.0.0.1:8000/api/config/status   (shows which services are configured, never the keys)"
