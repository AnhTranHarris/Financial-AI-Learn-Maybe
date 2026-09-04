param(
    [string]$VibeRoot = "C:\Users\lord1\DustyProviders\VibeTrading",
    [string]$Model = "qwen3:1.7b",
    [int]$HttpTimeoutSeconds = 90,
    [int]$AgentTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repo = Split-Path -Parent $PSScriptRoot
$dustyPython = Join-Path $repo ".venv\Scripts\python.exe"
$diagnostic = Join-Path $PSScriptRoot "diagnose_vibe_ollama_v2.py"
$validation = Join-Path $env:LOCALAPPDATA "DustyDragon\validation"
New-Item -ItemType Directory -Force -Path $validation | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$report = Join-Path $validation "m1143-vibe-ollama-$stamp.json"
$log = Join-Path $validation "m1143-vibe-ollama-$stamp.log"

if (-not (Test-Path -LiteralPath $dustyPython)) {
    throw "Dusty virtual environment Python missing: $dustyPython"
}
if (-not (Test-Path -LiteralPath $diagnostic)) {
    throw "Diagnostic module missing: $diagnostic"
}

Write-Host ""
Write-Host "M114.3 Vibe -> Ollama diagnostic v2"
Write-Host "Read-only A/B test: native Ollama vs OpenAI-compatible path used by Vibe."
Write-Host "No Vibe config changes, no MT5, no broker credentials, no orders."
Write-Host "Model: $Model"
Write-Host ""

$previousEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    $ErrorActionPreference = "Continue"
    & $dustyPython $diagnostic `
        --vibe-root $VibeRoot `
        --model $Model `
        --http-timeout $HttpTimeoutSeconds `
        --agent-timeout $AgentTimeoutSeconds `
        --report $report 2>&1 | Tee-Object -FilePath $log -Encoding UTF8
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
}
finally {
    [Console]::OutputEncoding = $previousEncoding
}

Write-Host ""
if ($code -eq 0) {
    Write-Host "M114.3 VIBE -> OLLAMA DIAGNOSTIC PASSED"
    Write-Host "Report: $report"
    Write-Host "Log:    $log"
    exit 0
}

Write-Host "M114.3 VIBE -> OLLAMA DIAGNOSTIC FOUND A BLOCKER"
Write-Host "Do not reinstall Vibe or Ollama."
Write-Host "Report: $report"
Write-Host "Log:    $log"
if (Test-Path -LiteralPath $report) {
    explorer.exe /select,"$report"
} elseif (Test-Path -LiteralPath $log) {
    explorer.exe /select,"$log"
}
exit $code
