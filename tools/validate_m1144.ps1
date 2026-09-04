param(
    [string]$VibeRoot = "C:\Users\lord1\DustyProviders\VibeTrading",
    [string]$WorkRoot = "$env:LOCALAPPDATA\DustyDragon\vibe-research-m1144",
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$validation = Join-Path $env:LOCALAPPDATA "DustyDragon\validation"
New-Item -ItemType Directory -Force -Path $validation | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$report = Join-Path $validation "m1144-vibe-research-$stamp.json"
$log = Join-Path $validation "m1144-vibe-research-$stamp.log"
$lines = New-Object System.Collections.Generic.List[string]

function Invoke-CapturedNative {
    param(
        [string]$Label,
        [string[]]$Arguments
    )
    $lines.Add("=== $Label ===")
    $captured = @()
    $code = 1
    try {
        $ErrorActionPreference = "Continue"
        $captured = @(& $python @Arguments 2>&1)
        if ($null -ne $LASTEXITCODE) {
            $code = [int]$LASTEXITCODE
        }
    }
    finally {
        $ErrorActionPreference = "Stop"
    }
    foreach ($item in $captured) {
        $text = if ($null -eq $item) { "" } else { $item.ToString() }
        $lines.Add($text)
        Write-Host $text
    }
    $lines.Add("exit_code=$code")
    return $code
}

$status = "fail"
$errorText = ""
try {
    Set-Location $repo
    $branch = (git branch --show-current).Trim()
    if ($branch -ne "carson/m1144-vibe-research-contractor") {
        throw "Expected carson/m1144-vibe-research-contractor but found $branch"
    }
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Dusty Python missing: $python"
    }
    if (-not (Test-Path -LiteralPath $VibeRoot)) {
        throw "Vibe root missing: $VibeRoot"
    }

    Write-Host ""
    Write-Host "M114.4 Vibe research-contractor validation"
    Write-Host "No Vibe LLM agent. No MT5. No broker credentials. No orders."
    Write-Host "Windows PowerShell: $($PSVersionTable.PSVersion)"
    Write-Host ""

    $testCode = Invoke-CapturedNative `
        -Label "targeted unit tests" `
        -Arguments @("-m", "unittest", "discover", "-s", "tests", "-p", "test_vibe_research_contractor.py", "-v")
    if ($testCode -ne 0) {
        throw "Targeted Vibe contractor tests failed with exit code $testCode"
    }

    $smokeCode = Invoke-CapturedNative `
        -Label "local Vibe research-tool smoke" `
        -Arguments @(
            "-m", "dusty.vibe_research_smoke",
            "--vibe-root", $VibeRoot,
            "--work-root", $WorkRoot,
            "--timeout", $TimeoutSeconds.ToString()
        )
    if ($smokeCode -ne 0) {
        throw "Vibe research-tool smoke failed with exit code $smokeCode"
    }

    $status = "pass"
}
catch {
    $errorText = $_.Exception.Message
    $lines.Add("ERROR: $errorText")
}
finally {
    Set-Content -LiteralPath $log -Value $lines -Encoding UTF8
    $head = ""
    try { $head = (git rev-parse HEAD).Trim() } catch { $head = "unknown" }
    $payload = [ordered]@{
        schema = "dusty-m1144-vibe-research-validation-v1"
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        status = $status
        branch = "carson/m1144-vibe-research-contractor"
        commit = $head
        vibe_root = $VibeRoot
        work_root = $WorkRoot
        safety = [ordered]@{
            llm_agent = $false
            mt5 = $false
            broker_credentials = $false
            orders = $false
            shell_tools = $false
        }
        error = $errorText
        log = $log
    }
    $payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $report -Encoding UTF8
}

Write-Host ""
if ($status -eq "pass") {
    Write-Host "M114.4 VIBE RESEARCH CONTRACTOR VALIDATION PASSED"
    Write-Host "Report: $report"
    Write-Host "Log:    $log"
    exit 0
}

Write-Host "M114.4 VIBE RESEARCH CONTRACTOR VALIDATION FAILED"
Write-Host "Error:  $errorText"
Write-Host "Report: $report"
Write-Host "Log:    $log"
if (Test-Path -LiteralPath $report) {
    explorer.exe /select,"$report"
}
exit 2
