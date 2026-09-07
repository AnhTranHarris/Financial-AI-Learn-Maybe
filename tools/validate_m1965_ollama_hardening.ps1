param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$ExpectedHead,

    [string]$Repository = (Split-Path -Parent $PSScriptRoot),

    [ValidateNotNullOrEmpty()]
    [string]$Model = 'qwen3:1.7b'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    & $FilePath @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "$FilePath exited with code $code while running: $($Arguments -join ' ')"
    }
}

function Get-NativeText {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    $lines = @(& $FilePath @Arguments)
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "$FilePath exited with code $code while running: $($Arguments -join ' ')"
    }
    if ($lines.Count -eq 0) {
        return ''
    }
    return (($lines | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

$repo = (Resolve-Path -LiteralPath $Repository).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$estate = Join-Path $env:LOCALAPPDATA 'DustyDragon\strategy-estate\reconstructions.json'

Set-Location -LiteralPath $repo

Write-Host ''
Write-Host '==================================================' -ForegroundColor Cyan
Write-Host 'M196.5 OLLAMA HARDENING — NATIVE VALIDATION' -ForegroundColor Cyan
Write-Host '==================================================' -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Dusty Python not found: $python"
}

$head = Get-NativeText 'git' @('rev-parse', 'HEAD')
$branch = Get-NativeText 'git' @('branch', '--show-current')
$dirty = @(Get-NativeText 'git' @('status', '--porcelain=v1', '--untracked-files=all')) |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

Write-Host "Branch: $branch"
Write-Host "HEAD:   $head"
Write-Host "Tree:   $(if ($dirty.Count -eq 0) { 'CLEAN' } else { 'DIRTY' })"

if ($head -ne $ExpectedHead) {
    throw "Expected exact HEAD $ExpectedHead but found $head"
}
if ([string]::IsNullOrWhiteSpace($branch)) {
    throw 'Detached HEAD is not accepted for native validation.'
}
if ($dirty.Count -ne 0) {
    throw 'Working tree is not clean. No validation workload was started.'
}

Write-Host ''
Write-Host '--- Compile gate ---' -ForegroundColor Cyan
Invoke-NativeChecked $python @('-m', 'compileall', '-q', 'src/dusty', 'tests', 'tools')
Write-Host 'Compile: PASS' -ForegroundColor Green

Write-Host ''
Write-Host '--- M196.5 focused regression gate ---' -ForegroundColor Cyan
Invoke-NativeChecked $python @(
    '-m', 'unittest', 'discover',
    '-s', 'tests',
    '-p', 'test_m1965*.py',
    '-v'
)
Write-Host 'M196.5 regressions: PASS' -ForegroundColor Green

Write-Host ''
Write-Host '--- Preserve current Strategy Estate ---' -ForegroundColor Cyan
if (Test-Path -LiteralPath $estate -PathType Leaf) {
    $hash = (Get-FileHash -LiteralPath $estate -Algorithm SHA256).Hash.ToLowerInvariant()
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = "$estate.pre-m1965-hardening-$stamp.bak"
    Copy-Item -LiteralPath $estate -Destination $backup -ErrorAction Stop
    Write-Host "Estate:  $estate"
    Write-Host "SHA256:  $hash"
    Write-Host "Backup:  $backup"
}
else {
    Write-Host 'No existing estate file found.'
}

Write-Host ''
Write-Host '--- Populate only missing governed starter proposals ---' -ForegroundColor Cyan
$clock = [System.Diagnostics.Stopwatch]::StartNew()
& $python -m dusty.strategy_estate_cli --seed-core --model $Model --require-all-seeds
$populationCode = $LASTEXITCODE
$clock.Stop()

Write-Host "Population exit code: $populationCode"
Write-Host "Population wall clock: $([Math]::Round($clock.Elapsed.TotalMinutes, 2)) minutes"

if ($populationCode -notin @(0, 4)) {
    throw "Strategy Estate population failed with unexpected exit code $populationCode"
}

Write-Host ''
Write-Host '--- Read-only governed-seed coverage check ---' -ForegroundColor Cyan
& $python -m dusty.strategy_estate_cli --list --require-all-seeds
$coverageCode = $LASTEXITCODE

if ($coverageCode -notin @(0, 4)) {
    throw "Strategy Estate coverage audit failed with unexpected exit code $coverageCode"
}

$finalHead = Get-NativeText 'git' @('rev-parse', 'HEAD')
$finalBranch = Get-NativeText 'git' @('branch', '--show-current')
$finalDirty = @(Get-NativeText 'git' @('status', '--porcelain=v1', '--untracked-files=all')) |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

if ($finalHead -ne $ExpectedHead) {
    throw 'Repository HEAD changed during validation.'
}
if ($finalBranch -ne $branch) {
    throw 'Repository branch changed during validation.'
}
if ($finalDirty.Count -ne 0) {
    throw 'Repository became dirty during validation.'
}

Write-Host ''
Write-Host '=================================================='
if ($coverageCode -eq 0) {
    Write-Host 'M196.5 NATIVE STRATEGY ESTATE VALIDATION PASSED' -ForegroundColor Green
    Write-Host 'All governed starter proposals are represented.' -ForegroundColor Green
}
else {
    Write-Host 'M196.5 SOFTWARE PASSED — HARDWARE POPULATION INCOMPLETE' -ForegroundColor Yellow
    Write-Host 'Successful candidates were preserved; no code rollback is warranted.' -ForegroundColor Yellow
}
Write-Host '=================================================='
Write-Host "Branch: $finalBranch"
Write-Host "HEAD:   $finalHead"
Write-Host 'Tree:   CLEAN'
Write-Host 'MT5:    NOT MODIFIED BY THIS HARNESS'
Write-Host 'Orders: NONE'

if ($coverageCode -eq 4) {
    exit 4
}
