param(
    [string]$ExpectedHead = "",
    [string]$TerminalPath = "",
    [string]$ProviderRoot = "",
    [string]$ValidationRoot = "",
    [string]$Symbol = "EURUSD",
    [string]$NativeSymbol = "",
    [string]$Timeframe = "M15",
    [int]$HistoryDays = 14,
    [string]$OllamaModel = "qwen3:1.7b",
    [switch]$FullSuite
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

$head = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not read Git HEAD."
}
if ($ExpectedHead -and $head -ne $ExpectedHead) {
    throw "Wrong Dusty commit. Expected $ExpectedHead but found $head"
}

$dirty = @(git status --porcelain)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect repository state."
}
if ($dirty.Count -gt 0) {
    throw "Dusty repository is not clean. Nothing will be discarded."
}

$python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $python = $pythonCommand.Source
}

if (-not $ProviderRoot) {
    $ProviderRoot = Join-Path $HOME "DustyProviders"
}
if (-not $ValidationRoot) {
    if (-not $env:LOCALAPPDATA) {
        throw "LOCALAPPDATA is unavailable and no ValidationRoot was supplied."
    }
    $ValidationRoot = Join-Path $env:LOCALAPPDATA "DustyDragon\validation"
}
New-Item -ItemType Directory -Force -Path $ValidationRoot | Out-Null

Write-Host "M154.1 local workstation certification"
Write-Host ("HEAD: " + $head)
Write-Host "Safety: read-only MT5 history; no orders, broker writes, risk override, entry veto, or Champion promotion."

$softwareArgs = @{
    ExpectedHead = $head
    ValidationRoot = $ValidationRoot
}
if ($FullSuite) {
    & (Join-Path $Repo "tools\validate_m135_m154.ps1") @softwareArgs -FullSuite
}
else {
    & (Join-Path $Repo "tools\validate_m135_m154.ps1") @softwareArgs
}
if ($LASTEXITCODE -ne 0) {
    throw "M135-M154 software validation failed. Hardware certification was not started."
}

function Resolve-MT5Terminal {
    param([string]$ExplicitPath)

    if ($ExplicitPath) {
        $resolved = [System.IO.Path]::GetFullPath($ExplicitPath)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "Specified MT5 terminal does not exist: $resolved"
        }
        return $resolved
    }

    $candidates = @()
    try {
        $candidates += @(
            Get-Process -Name "terminal64" -ErrorAction SilentlyContinue |
                ForEach-Object {
                    try { $_.Path } catch { $null }
                } |
                Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
        )
    }
    catch {
        # Process discovery is optional; filesystem discovery follows.
    }

    $roots = @()
    if ($env:ProgramFiles) { $roots += $env:ProgramFiles }
    if (${env:ProgramFiles(x86)}) { $roots += ${env:ProgramFiles(x86)} }
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA "Programs") }

    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) {
            continue
        }
        foreach ($directory in @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue)) {
            $candidate = Join-Path $directory.FullName "terminal64.exe"
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $candidates += $candidate
            }
        }
    }

    $unique = @($candidates | ForEach-Object { [System.IO.Path]::GetFullPath($_) } | Sort-Object -Unique)
    if ($unique.Count -eq 1) {
        return $unique[0]
    }
    if ($unique.Count -eq 0) {
        throw "No MT5 terminal64.exe was found automatically. Re-run with -TerminalPath 'C:\...\terminal64.exe'."
    }

    Write-Host ""
    Write-Host "Multiple MT5 terminals were found. Dusty will not guess:"
    foreach ($candidate in $unique) {
        Write-Host ("  " + $candidate)
    }
    throw "MT5 terminal identity is ambiguous. Re-run with -TerminalPath using the intended terminal."
}

$resolvedTerminal = Resolve-MT5Terminal -ExplicitPath $TerminalPath
Write-Host ("MT5 terminal: " + $resolvedTerminal)
Write-Host ("Provider root: " + $ProviderRoot)
Write-Host ("Ollama model: " + $OllamaModel)

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runRoot = Join-Path $ValidationRoot ("m1541-hardware-" + $stamp)
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
$report = Join-Path $runRoot "report.json"
$stdoutPath = Join-Path $runRoot "hardware.stdout.txt"
$stderrPath = Join-Path $runRoot "hardware.stderr.txt"
$workRoot = Join-Path $runRoot "work"

$script = Join-Path $Repo "tools\smoke_m135_m154_hardware.py"
$native = if ($NativeSymbol) { $NativeSymbol } else { $Symbol }

$arguments = @(
    "`"$script`"",
    "--terminal-path", "`"$resolvedTerminal`"",
    "--provider-root", "`"$ProviderRoot`"",
    "--work-root", "`"$workRoot`"",
    "--report", "`"$report`"",
    "--symbol", $Symbol,
    "--native-symbol", $native,
    "--timeframe", $Timeframe,
    "--history-days", [string]$HistoryDays,
    "--ollama-model", $OllamaModel
)

$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $Repo "src"
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList $arguments `
        -WorkingDirectory $Repo `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

foreach ($path in @($stdoutPath, $stderrPath)) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        foreach ($line in @(Get-Content -LiteralPath $path)) {
            Write-Host $line
        }
    }
}

if (-not (Test-Path -LiteralPath $report -PathType Leaf)) {
    throw "Hardware certification did not write a report."
}
$payload = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if ($process.ExitCode -ne 0 -or $payload.status -ne "pass") {
    Write-Host ""
    Write-Host "M154.1 LOCAL HARDWARE CERTIFICATION FAILED"
    Write-Host "Do not reinstall MT5, forecast providers, or Ollama."
    Write-Host ("Report: " + $report)
    Write-Host ("Stdout: " + $stdoutPath)
    Write-Host ("Stderr: " + $stderrPath)
    exit 2
}

if (
    $payload.safety.mt5_orders -or
    $payload.safety.broker_credentials -or
    $payload.safety.broker_write -or
    $payload.safety.entry_veto -or
    $payload.safety.promotion -or
    $payload.safety.risk_override
) {
    throw "Hardware report violated the research-only safety contract."
}
if ($payload.forecast_contractors.forecast_skill_claimed) {
    throw "Hardware smoke incorrectly claimed forecast skill."
}

Write-Host ""
Write-Host "M154.1 LOCAL HARDWARE CERTIFICATION PASSED"
Write-Host ("Report: " + $report)
Write-Host ("Stdout: " + $stdoutPath)
Write-Host ("Stderr: " + $stderrPath)
exit 0
