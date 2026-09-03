param(
    [string]$ProviderRoot = "C:\Users\lord1\DustyProviders"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedBranch = "carson/m114-multi-forecast-contractors"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ValidationRoot = Join-Path $env:LOCALAPPDATA "DustyDragon\validation"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $ValidationRoot "m114-$Timestamp.log"
$ReportPath = Join-Path $ValidationRoot "m114-$Timestamp.json"
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
    if ($LASTEXITCODE -ne 0 -or -not $LocalCommit -or $LocalCommit -ne $RemoteCommit) {
        throw "Local HEAD does not match the current GitHub M114 branch."
    }

    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Dusty virtual-environment Python is missing: $Python"
    }

    Write-Host ""
    Write-Host "M114 exact branch verified: $LocalCommit"
    Write-Host "Compiling Dusty and running forecast-contractor gates..."

    & $Python -m compileall -q src tests
    if ($LASTEXITCODE -ne 0) {
        throw "Python compile gate failed."
    }

    foreach ($TestFile in @(
        "test_provider_registry.py",
        "test_provider_process.py",
        "test_provider_forecast_adapter.py",
        "test_provider_forecast_service.py",
        "test_provider_multi.py"
    )) {
        & $Python -m unittest discover -s tests -p $TestFile -v
        if ($LASTEXITCODE -ne 0) {
            throw "Provider gate failed: $TestFile"
        }
    }

    Write-Host ""
    Write-Host "Running full Dusty regression suite..."
    & $Python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) {
        throw "Full Dusty regression suite failed."
    }

    Write-Host ""
    Write-Host "Running M114 all-three hardware validation."
    Write-Host "Models start sequentially and remain in isolated persistent workers."
    Write-Host "Two synthetic forecast rounds reuse the same PIDs."
    Write-Host "No MT5 connection. No account data. No broker credentials. No orders."
    Write-Host ""

    $ServiceLines = @(
        & $Python -m dusty.provider_multi_smoke `
            --provider-root $ProviderRoot `
            --rounds 2
    )
    $ServiceExit = $LASTEXITCODE

    foreach ($Line in $ServiceLines) {
        Write-Host $Line
    }
    if ($ServiceExit -ne 0) {
        throw "M114 all-three hardware validation returned exit code $ServiceExit."
    }

    $Events = @()
    foreach ($Line in $ServiceLines) {
        try {
            $Events += ($Line | ConvertFrom-Json)
        }
        catch {
            throw "M114 smoke output contained a non-JSON line: $Line"
        }
    }

    $Startups = @($Events | Where-Object { $_.event -eq "startup" })
    $Forecasts = @($Events | Where-Object { $_.event -eq "forecast" })
    $Shutdown = @($Events | Where-Object { $_.event -eq "shutdown" })

    if ($Startups.Count -ne 3) {
        throw "Expected exactly three provider startup events."
    }
    foreach ($Startup in $Startups) {
        if ($Startup.state -ne "ready" -or $null -eq $Startup.pid) {
            throw "A selected provider did not reach READY with a process ID."
        }
    }
    if ($Forecasts.Count -ne 6) {
        throw "Expected exactly six forecast results: three providers times two rounds."
    }
    foreach ($Forecast in $Forecasts) {
        if ($Forecast.status -ne "available" -or $null -eq $Forecast.p50) {
            throw "A provider forecast was not AVAILABLE."
        }
        if (
            $Forecast.authority.broker_write -ne $false -or
            $Forecast.authority.entry_veto -ne $false -or
            $Forecast.authority.promotion -ne $false
        ) {
            throw "Authority invariant failed in a provider forecast."
        }
    }
    if ($Shutdown.Count -ne 1) {
        throw "Expected one all-provider shutdown event."
    }
    foreach ($Name in @("chronos2", "kronos-small", "timesfm-2.5")) {
        if ($Shutdown[0].states.$Name -ne "stopped") {
            throw "$Name did not shut down cleanly."
        }
    }

    $Report = [ordered]@{
        milestone = "M114"
        status = "PASS"
        branch = $ExpectedBranch
        commit = $LocalCommit
        provider_root = $ProviderRoot
        providers = @($Startups | ForEach-Object {
            [ordered]@{
                provider_id = $_.provider_id
                startup_state = $_.state
                pid = $_.pid
            }
        })
        forecasts = @($Forecasts | ForEach-Object {
            [ordered]@{
                round = $_.round
                provider_id = $_.provider_id
                status = $_.status
                distribution_method = $_.distribution_method
                sample_count = $_.sample_count
                pid = $_.pid
                p50 = $_.p50
                fingerprint = $_.fingerprint
            }
        })
        broker_write_authority = $false
        entry_veto_authority = $false
        promotion_authority = $false
        log_path = $LogPath
    }
    $Report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8

    Write-Host ""
    Write-Host "M114 LOCAL VALIDATION PASSED"
    Write-Host "Report: $ReportPath"
    Write-Host "Log:    $LogPath"
}
catch {
    $ExitCode = 1
    $ErrorText = $_.Exception.Message
    [ordered]@{
        milestone = "M114"
        status = "FAIL"
        error = $ErrorText
        log_path = $LogPath
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ReportPath -Encoding UTF8

    Write-Host ""
    Write-Host "M114 LOCAL VALIDATION FAILED"
    Write-Host "Error:  $ErrorText"
    Write-Host "Report: $ReportPath"
    Write-Host "Log:    $LogPath"
}
finally {
    Stop-Transcript | Out-Null
}

exit $ExitCode
