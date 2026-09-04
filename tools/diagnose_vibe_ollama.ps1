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
Write-Host "Windows PowerShell: $($PSVersionTable.PSVersion)"
Write-Host ""

# Windows PowerShell 5.1 Tee-Object has no -Encoding parameter.  Capture the
# bounded diagnostic output first, then display it and persist it with
# Set-Content -Encoding UTF8, which is supported on 5.1.  Initialize the exit
# code before invocation so an unexpected wrapper exception can never leave an
# undefined variable under Set-StrictMode.
$code = 1
$captured = @()
$previousEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    $ErrorActionPreference = "Continue"
    try {
        $captured = @(
            & $dustyPython $diagnostic `
                --vibe-root $VibeRoot `
                --model $Model `
                --http-timeout $HttpTimeoutSeconds `
                --agent-timeout $AgentTimeoutSeconds `
                --report $report 2>&1
        )
        if ($null -ne $LASTEXITCODE) {
            $code = [int]$LASTEXITCODE
        }
    }
    catch {
        $captured += $_
        $code = 1
    }
    finally {
        $ErrorActionPreference = "Stop"
    }
}
finally {
    [Console]::OutputEncoding = $previousEncoding
}

$textLines = @(
    $captured | ForEach-Object {
        if ($null -eq $_) {
            ""
        }
        else {
            $_.ToString()
        }
    }
)

$textLines | ForEach-Object { Write-Host $_ }
Set-Content -LiteralPath $log -Value $textLines -Encoding UTF8

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
