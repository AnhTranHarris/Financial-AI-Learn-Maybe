param(
    [string]$ProviderRoot = "C:\Users\lord1\DustyProviders"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedBranch = "carson/m113-persistent-chronos-worker"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ValidationRoot = Join-Path $env:LOCALAPPDATA "DustyDragon\validation"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $ValidationRoot "m113-$Timestamp.log"
$ReportPath = Join-Path $ValidationRoot "m113-$Timestamp.json"
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
        throw "Repository is dirty. Validation refuses to continue."
    }

    git fetch origin $ExpectedBranch
    if ($LASTEXITCODE -ne 0) {
        throw "Git fetch failed."
    }

    $LocalCommit = (git rev-parse HEAD).Trim()
    $RemoteCommit = (git rev-parse "origin/$ExpectedBranch").Trim()
    if (
        $LASTEXITCODE -ne 0 -or
        -not $LocalCommit -or
        $LocalCommit -ne $RemoteCommit
    ) {
        throw "Local HEAD does not match the current GitHub M113 branch."
    }

    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Dusty virtual-environment Python is missing: $Python"
    }

    Write-Host ""
    Write-Host "M113 exact branch verified: $LocalCommit"
    Write-Host "Running persistent-provider unit gates..."

    & $Python -m unittest discover -s tests -p "test_provider_process.py" -v
    if ($LASTEXITCODE -ne 0) {
        throw "Persistent process unit gate failed."
    }

    & $Python -m unittest discover -s tests -p "test_provider_forecast_service.py" -v
    if ($LASTEXITCODE -ne 0) {
        throw "Persistent Chronos service unit gate failed."
    }

    & $Python -m unittest discover -s tests -p "test_provider_forecast_adapter.py" -v
    if ($LASTEXITCODE -ne 0) {
        throw "M112 adapter regression gate failed."
    }

    Write-Host ""
    Write-Host "Running full Dusty regression suite..."
    & $Python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) {
        throw "Full Dusty regression suite failed."
    }

    Write-Host ""
    Write-Host "Running M113 local hardware smoke test."
    Write-Host "Chronos will start once and serve two forecasts through the same PID."
    Write-Host "No MT5 connection. No broker credentials. No orders."
    Write-Host ""

    $ServiceLines = @(
        & $Python -m dusty.provider_forecast_service `
            --provider-root $ProviderRoot `
            --smoke-test `
            --count 2 `
            --startup-timeout 300 `
            --request-timeout 180
    )
    $ServiceExit = $LASTEXITCODE

    foreach ($Line in $ServiceLines) {
        Write-Host $Line
    }
    if ($ServiceExit -ne 0) {
        throw "Persistent Chronos hardware smoke test returned exit code $ServiceExit."
    }

    $Events = @()
    foreach ($Line in $ServiceLines) {
        try {
            $Events += ($Line | ConvertFrom-Json)
        }
        catch {
            throw "M113 smoke output contained a non-JSON line: $Line"
        }
    }

    $Startup = @($Events | Where-Object { $_.event -eq "startup" })
    $Forecasts = @($Events | Where-Object { $_.event -eq "forecast" })
    $Shutdown = @($Events | Where-Object { $_.event -eq "shutdown" })

    if ($Startup.Count -ne 1 -or $Startup[0].state -ne "ready") {
        throw "Chronos did not reach READY exactly once."
    }
    if ($Forecasts.Count -ne 2) {
        throw "Expected exactly two persistent forecasts."
    }
    foreach ($Forecast in $Forecasts) {
        if (
            $Forecast.state -ne "ready" -or
            $Forecast.result.status -ne "available" -or
            $null -eq $Forecast.result.evidence
        ) {
            throw "A persistent forecast was not AVAILABLE."
        }
        if (
            $Forecast.result.evidence.broker_write_authority -ne $false -or
            $Forecast.result.evidence.entry_veto_authority -ne $false -or
            $Forecast.result.evidence.promotion_authority -ne $false
        ) {
            throw "Authority invariant failed in returned forecast evidence."
        }
    }
    if ($Shutdown.Count -ne 1 -or $Shutdown[0].state -ne "stopped") {
        throw "Chronos did not shut down cleanly."
    }

    $Report = [ordered]@{
        milestone = "M113"
        status = "PASS"
        branch = $ExpectedBranch
        commit = $LocalCommit
        provider = "chronos2"
        startup_state = $Startup[0].state
        startup_seconds = $Startup[0].startup_seconds
        pid = $Startup[0].pid
        forecast_seconds = @($Forecasts | ForEach-Object { $_.elapsed_seconds })
        forecast_statuses = @($Forecasts | ForEach-Object { $_.result.status })
        shutdown_state = $Shutdown[0].state
        broker_write_authority = $false
        entry_veto_authority = $false
        promotion_authority = $false
        log_path = $LogPath
    }
    $Report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ReportPath -Encoding UTF8

    Write-Host ""
    Write-Host "M113 LOCAL VALIDATION PASSED"
    Write-Host "Report: $ReportPath"
    Write-Host "Log:    $LogPath"
}
catch {
    $ExitCode = 1
    $ErrorText = $_.Exception.Message
    [ordered]@{
        milestone = "M113"
        status = "FAIL"
        error = $ErrorText
        log_path = $LogPath
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ReportPath -Encoding UTF8

    Write-Host ""
    Write-Host "M113 LOCAL VALIDATION FAILED"
    Write-Host "Error:  $ErrorText"
    Write-Host "Report: $ReportPath"
    Write-Host "Log:    $LogPath"
}
finally {
    Stop-Transcript | Out-Null
}

exit $ExitCode
