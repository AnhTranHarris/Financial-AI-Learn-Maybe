[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedHead,

    [Parameter(Mandatory = $true)]
    [string]$TerminalPath,

    [string]$Repo = (Get-Location).Path,
    [string]$NativeSymbol = 'EURUSD',
    [string]$Timeframe = 'M15',
    [string]$FromDate = '2026-08-31',
    [string]$ToDate = '2026-09-01',
    [string]$EntryTime = '10:00:00',
    [string]$ExitTime = '11:00:00',
    [double]$Volume = 0.01,
    [double]$StopPrice = 0.1,
    [ValidateRange(30, 3600)]
    [int]$TimeoutSeconds = 180,
    [string]$ValidationRoot = '',
    [switch]$FullSuite
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    if ($full.Length -le $root.Length) {
        return $full
    }
    return $full.TrimEnd([char[]]@('\', '/'))
}

function Assert-LastExitCode {
    param([Parameter(Mandatory = $true)][string]$Label)
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Get-TerminalProcesses {
    return @(Get-CimInstance Win32_Process -Filter "Name='terminal64.exe'")
}

function Assert-TerminalPathIdle {
    param([Parameter(Mandatory = $true)][string]$TargetTerminal)
    $target = Get-NormalizedPath $TargetTerminal
    $rows = Get-TerminalProcesses
    $unknown = @($rows | Where-Object { -not $_.ExecutablePath })
    if ($unknown.Count -gt 0) {
        throw 'At least one terminal64.exe process has no readable ExecutablePath; terminal identity is unverifiable.'
    }
    $conflicts = @(
        $rows | Where-Object {
            (Get-NormalizedPath $_.ExecutablePath) -ieq $target
        }
    )
    if ($conflicts.Count -gt 0) {
        $pids = ($conflicts | ForEach-Object { $_.ProcessId }) -join ', '
        throw "The intended MT5 terminal is already running (PID(s): $pids). Close only that terminal before M161 certification."
    }
}

function Resolve-TerminalDataRoot {
    param([Parameter(Mandatory = $true)][string]$TargetTerminal)
    $installRoot = Get-NormalizedPath (Split-Path -Parent $TargetTerminal)
    $terminalRoot = Join-Path $env:APPDATA 'MetaQuotes\Terminal'
    if (-not (Test-Path -LiteralPath $terminalRoot -PathType Container)) {
        throw "MetaQuotes terminal data root not found: $terminalRoot"
    }
    $matches = @()
    foreach ($candidate in Get-ChildItem -LiteralPath $terminalRoot -Directory -ErrorAction Stop) {
        if ($candidate.Name -ieq 'Common') {
            continue
        }
        $origin = Join-Path $candidate.FullName 'origin.txt'
        if (-not (Test-Path -LiteralPath $origin -PathType Leaf)) {
            continue
        }
        try {
            $originText = (Get-Content -LiteralPath $origin -Raw -ErrorAction Stop).Trim()
            if ($originText -and (Get-NormalizedPath $originText) -ieq $installRoot) {
                $matches += $candidate.FullName
            }
        }
        catch {
            continue
        }
    }
    if ($matches.Count -ne 1) {
        throw "Expected exactly one terminal data directory whose origin.txt matches '$installRoot'; found $($matches.Count)."
    }
    return Get-NormalizedPath $matches[0]
}

$Repo = Get-NormalizedPath $Repo
$TerminalPath = Get-NormalizedPath $TerminalPath
$ExpectedHead = $ExpectedHead.ToLowerInvariant()

if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    throw "Repository directory does not exist: $Repo"
}
if (-not (Test-Path -LiteralPath $TerminalPath -PathType Leaf)) {
    throw "MT5 terminal does not exist: $TerminalPath"
}
if ([IO.Path]::GetFileName($TerminalPath) -ine 'terminal64.exe') {
    throw 'M161 hardware certification requires an explicit terminal64.exe path.'
}

$targetExpert = $null
$priorExpertBackup = $null
$targetExpertExisted = $false
$validationPassed = $false
$resolvedValidationRoot = $null

Push-Location $Repo
try {
    $actualHead = (& git rev-parse HEAD).Trim().ToLowerInvariant()
    Assert-LastExitCode 'git rev-parse HEAD'
    if ($actualHead -ne $ExpectedHead) {
        throw "Repository HEAD mismatch. Expected $ExpectedHead but found $actualHead."
    }
    $status = @(& git status --porcelain)
    Assert-LastExitCode 'git status --porcelain'
    if ($status.Count -gt 0) {
        throw 'Repository working tree is not clean. Preserve or stash local work before M161 hardware certification.'
    }

    $python = Join-Path $Repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Dusty virtual-environment Python not found: $python"
    }

    if (-not $ValidationRoot) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $ValidationRoot = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m161-hardware-$stamp"
    }
    $ValidationRoot = Get-NormalizedPath $ValidationRoot
    $resolvedValidationRoot = $ValidationRoot
    New-Item -ItemType Directory -Path $ValidationRoot -Force | Out-Null

    Write-Host '=== M161 LOCAL WINDOWS SOFTWARE PREFLIGHT ==='
    Write-Host "Repository: $Repo"
    Write-Host "HEAD:       $actualHead"
    Write-Host "Terminal:   $TerminalPath"
    Write-Host "Evidence:   $ValidationRoot"

    $env:PYTHONPATH = Join-Path $Repo 'src'
    & $python -m unittest `
        tests.test_m161_native_mt5_executor `
        tests.test_m161_mt5_set_contract `
        tests.test_m161_hardware_contract `
        tests.test_m67_tester_contract `
        -v
    Assert-LastExitCode 'M161 focused software gate'

    if ($FullSuite) {
        Write-Host '=== M161 OPTIONAL FULL LOCAL SUITE ==='
        & $python -m unittest discover -s tests -v
        Assert-LastExitCode 'Full local unittest suite'
    }

    Assert-TerminalPathIdle $TerminalPath

    $dataRoot = Resolve-TerminalDataRoot $TerminalPath
    $mql5Root = Join-Path $dataRoot 'MQL5'
    $tradeInclude = Join-Path $mql5Root 'Include\Trade\Trade.mqh'
    if (-not (Test-Path -LiteralPath $tradeInclude -PathType Leaf)) {
        throw "Target terminal MQL5 standard library not found: $tradeInclude"
    }

    $installRoot = Split-Path -Parent $TerminalPath
    $metaEditor = Join-Path $installRoot 'metaeditor64.exe'
    if (-not (Test-Path -LiteralPath $metaEditor -PathType Leaf)) {
        $fallback = Join-Path $installRoot 'metaeditor.exe'
        if (Test-Path -LiteralPath $fallback -PathType Leaf) {
            $metaEditor = $fallback
        }
        else {
            throw "MetaEditor executable was not found beside the target terminal: $installRoot"
        }
    }

    Write-Host '=== M161 TERMINAL IDENTITY ==='
    Write-Host "Data root:  $dataRoot"
    Write-Host "MetaEditor: $metaEditor"

    $compileRoot = Join-Path $ValidationRoot 'compile'
    New-Item -ItemType Directory -Path $compileRoot -Force | Out-Null
    $source = Join-Path $Repo 'mt5\DustyResearchEA.mq5'
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "DustyResearchEA source not found: $source"
    }
    $sourceCopy = Join-Path $compileRoot 'DustyResearchEA.mq5'
    Copy-Item -LiteralPath $source -Destination $sourceCopy -Force
    $compileLog = Join-Path $compileRoot 'DustyResearchEA.log'
    $compiledExpert = Join-Path $compileRoot 'DustyResearchEA.ex5'
    $compileExitPath = Join-Path $compileRoot 'metaeditor-exit-code.txt'
    Remove-Item -LiteralPath $compileLog -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $compiledExpert -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $compileExitPath -Force -ErrorAction SilentlyContinue

    Write-Host '=== M161 METAEDITOR COMPILE ==='
    $compileArgs = @(
        ('/compile:"{0}"' -f $sourceCopy),
        ('/include:"{0}"' -f $mql5Root),
        '/log'
    )
    $compileProcess = Start-Process `
        -FilePath $metaEditor `
        -ArgumentList $compileArgs `
        -WorkingDirectory $compileRoot `
        -Wait `
        -PassThru
    $compileExitCode = [int]$compileProcess.ExitCode
    Set-Content -LiteralPath $compileExitPath -Value $compileExitCode -Encoding ASCII

    # MetaQuotes documents the compilation log and generated EX5 as the
    # compilation result but does not document a conventional 0=success
    # process-exit contract. MetaEditor community integrations have also
    # observed exit code 1 on successful CLI compilation. Therefore the exit
    # code is retained as diagnostic evidence while success remains fail-closed
    # on a fresh log with zero errors/warnings plus a fresh EX5 artifact.
    if (-not (Test-Path -LiteralPath $compileLog -PathType Leaf)) {
        throw "MetaEditor compile log not found after process exit $compileExitCode`: $compileLog"
    }
    $compileText = Get-Content -LiteralPath $compileLog -Raw
    if ($compileText -notmatch '(?i)0\s+errors?,\s*0\s+warnings?') {
        Write-Host $compileText
        throw "DustyResearchEA did not compile with zero errors and zero warnings (MetaEditor exit $compileExitCode)."
    }
    if (-not (Test-Path -LiteralPath $compiledExpert -PathType Leaf)) {
        throw "Compiled DustyResearchEA.ex5 not found after zero-error compile log (MetaEditor exit $compileExitCode): $compiledExpert"
    }
    Write-Host "MetaEditor process exit code: $compileExitCode (diagnostic only; log + EX5 prove compile success)."

    $expertRelativePath = 'DustyDragon/M161/DustyResearchEA.ex5'
    $targetExpertDir = Join-Path $mql5Root 'Experts\DustyDragon\M161'
    $targetExpert = Join-Path $targetExpertDir 'DustyResearchEA.ex5'
    New-Item -ItemType Directory -Path $targetExpertDir -Force | Out-Null
    $targetExpertExisted = Test-Path -LiteralPath $targetExpert -PathType Leaf
    if ($targetExpertExisted) {
        $priorExpertBackup = Join-Path $ValidationRoot 'prior-DustyResearchEA.ex5'
        Copy-Item -LiteralPath $targetExpert -Destination $priorExpertBackup -Force
    }
    Copy-Item -LiteralPath $compiledExpert -Destination $targetExpert -Force

    $terminalSha = (Get-FileHash -LiteralPath $TerminalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $expertSha = (Get-FileHash -LiteralPath $targetExpert -Algorithm SHA256).Hash.ToLowerInvariant()
    $sourceSha = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath (Join-Path $ValidationRoot 'source.sha256.txt') -Value $sourceSha -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $ValidationRoot 'terminal.sha256.txt') -Value $terminalSha -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $ValidationRoot 'expert.sha256.txt') -Value $expertSha -Encoding ASCII

    $commonFilesRoot = Join-Path $env:APPDATA 'MetaQuotes\Terminal\Common\Files'
    New-Item -ItemType Directory -Path $commonFilesRoot -Force | Out-Null
    $workRoot = Join-Path $ValidationRoot 'work'
    $reportPath = Join-Path $ValidationRoot 'report.json'

    Assert-TerminalPathIdle $TerminalPath

    Write-Host '=== M161 NATIVE STRATEGY TESTER CERTIFICATION ==='
    Write-Host "Native symbol: $NativeSymbol"
    Write-Host "Window:        $FromDate -> $ToDate"
    Write-Host 'Research-only: no broker-write authority; no strategy verdict.'

    $smokeArgs = @(
        (Join-Path $Repo 'tools\smoke_m161_hardware.py'),
        '--repo', $Repo,
        '--expected-head', $ExpectedHead,
        '--terminal-path', $TerminalPath,
        '--terminal-data-root', $dataRoot,
        '--common-files-root', $commonFilesRoot,
        '--expert-relative-path', $expertRelativePath,
        '--terminal-sha256', $terminalSha,
        '--expert-sha256', $expertSha,
        '--work-root', $workRoot,
        '--report', $reportPath,
        '--native-symbol', $NativeSymbol,
        '--timeframe', $Timeframe,
        '--from-date', $FromDate,
        '--to-date', $ToDate,
        '--entry-time', $EntryTime,
        '--exit-time', $ExitTime,
        '--volume', $Volume.ToString([Globalization.CultureInfo]::InvariantCulture),
        '--stop-price', $StopPrice.ToString([Globalization.CultureInfo]::InvariantCulture),
        '--timeout-seconds', $TimeoutSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
    )
    & $python @smokeArgs
    $smokeExit = $LASTEXITCODE

    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw "M161 hardware report not found: $reportPath"
    }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    Write-Host "Report: $reportPath"
    if ($smokeExit -ne 0 -or -not $report.passed) {
        Write-Host (Get-Content -LiteralPath $reportPath -Raw)
        throw "M161 native Strategy Tester certification failed (exit $smokeExit)."
    }

    $leftover = @(
        Get-TerminalProcesses | Where-Object {
            $_.ExecutablePath -and (Get-NormalizedPath $_.ExecutablePath) -ieq $TerminalPath
        }
    )
    if ($leftover.Count -gt 0) {
        $pids = ($leftover | ForEach-Object { $_.ProcessId }) -join ', '
        throw "M161 evidence passed but the target terminal remained running (PID(s): $pids). No automatic broad cleanup was attempted."
    }

    $validationPassed = $true
}
finally {
    $cleanupError = $null
    try {
        if ($targetExpert) {
            if ($targetExpertExisted) {
                if (-not $priorExpertBackup -or -not (Test-Path -LiteralPath $priorExpertBackup -PathType Leaf)) {
                    throw 'Prior M161 research EA backup is missing; cannot restore the pre-certification file.'
                }
                Copy-Item -LiteralPath $priorExpertBackup -Destination $targetExpert -Force
            }
            elseif (Test-Path -LiteralPath $targetExpert -PathType Leaf) {
                Remove-Item -LiteralPath $targetExpert -Force
            }
        }
    }
    catch {
        $cleanupError = $_.Exception.Message
    }
    Pop-Location
    if ($cleanupError) {
        if ($validationPassed) {
            throw "M161 certification evidence passed but transactional EA cleanup failed: $cleanupError"
        }
        Write-Warning "M161 cleanup also failed after certification failure: $cleanupError"
    }
}

if ($validationPassed) {
    Write-Host ''
    Write-Host 'M161 LOCAL HARDWARE CERTIFICATION PASSED'
    Write-Host "Evidence directory: $resolvedValidationRoot"
    Write-Host 'The staged research EA was restored/removed transactionally.'
    Write-Host 'This certifies the native research boundary only; it grants no demo/live trading authority.'
}