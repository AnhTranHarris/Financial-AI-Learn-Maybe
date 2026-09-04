param(
    [string]$ProviderRoot = "C:\Users\lord1\DustyProviders",
    [int]$TimeoutSeconds = 180
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedBranch = "carson/m1141-startup-diagnostics"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ValidationRoot = Join-Path $env:LOCALAPPDATA "DustyDragon\validation"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $ValidationRoot "m1141-startup-$Timestamp.log"
$ReportPath = Join-Path $ValidationRoot "m1141-startup-$Timestamp.json"
$ExitCode = 0

New-Item -ItemType Directory -Force -Path $ValidationRoot | Out-Null
Start-Transcript -Path $LogPath -Force | Out-Null

try {
    Set-Location $RepoRoot

    $Branch = (git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $Branch -ne $ExpectedBranch) {
        throw "Expected branch $ExpectedBranch but found '$Branch'."
    }

    $Dirty = git status --porcelain
    if ($LASTEXITCODE -ne 0 -or $Dirty) {
        throw "Repository is dirty. Diagnostic refuses to continue."
    }

    git fetch origin $ExpectedBranch
    if ($LASTEXITCODE -ne 0) {
        throw "Git fetch failed."
    }

    $LocalCommit = (git rev-parse HEAD).Trim()
    $RemoteCommit = (git rev-parse "origin/$ExpectedBranch").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $LocalCommit -or $LocalCommit -ne $RemoteCommit) {
        throw "Local HEAD does not match the current GitHub M114.1 diagnostic branch."
    }

    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Dusty virtual-environment Python is missing: $Python"
    }

    Write-Host ""
    Write-Host "M114.1 startup diagnostic branch verified: $LocalCommit"
    Write-Host "This does NOT rerun the 564-test suite."
    Write-Host "Each forecasting environment is tested independently and then exits."
    Write-Host "No MT5 connection. No account data. No broker credentials. No orders."
    Write-Host "Per-provider timeout: $TimeoutSeconds seconds"
    Write-Host ""

    & $Python -m compileall -q src\dusty\provider_startup_diagnostics.py
    if ($LASTEXITCODE -ne 0) {
        throw "Startup diagnostic module did not compile."
    }

    & $Python -m dusty.provider_startup_diagnostics `
        --provider-root $ProviderRoot `
        --timeout $TimeoutSeconds `
        --report $ReportPath
    $ProbeExit = $LASTEXITCODE

    Write-Host ""
    if ($ProbeExit -eq 0) {
        Write-Host "M114.1 DIRECT PROVIDER STARTUP PROBE PASSED"
    }
    else {
        Write-Host "M114.1 DIRECT PROVIDER STARTUP PROBE COMPLETED WITH DIAGNOSTIC FAILURES"
        Write-Host "The report identifies the last completed stage for each provider."
        Write-Host "Do not reinstall anything."
    }
    Write-Host "Report: $ReportPath"
    Write-Host "Log:    $LogPath"
    $ExitCode = $ProbeExit
}
catch {
    $ExitCode = 1
    $ErrorText = $_.Exception.Message
    [ordered]@{
        milestone = "M114.1"
        status = "DIAGNOSTIC_SCRIPT_FAILURE"
        error = $ErrorText
        log_path = $LogPath
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ReportPath -Encoding UTF8

    Write-Host ""
    Write-Host "M114.1 DIAGNOSTIC SCRIPT FAILED"
    Write-Host "Error:  $ErrorText"
    Write-Host "Report: $ReportPath"
    Write-Host "Log:    $LogPath"
}
finally {
    Stop-Transcript | Out-Null
}

exit $ExitCode
