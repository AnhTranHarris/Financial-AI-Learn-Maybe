param(
    [string]$ExpectedHead = "",
    [string]$ValidationRoot = "",
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

if (-not $ValidationRoot) {
    if (-not $env:LOCALAPPDATA) {
        throw "LOCALAPPDATA is unavailable and no ValidationRoot was supplied."
    }
    $ValidationRoot = Join-Path $env:LOCALAPPDATA "DustyDragon\validation"
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runRoot = Join-Path $ValidationRoot "m115-m134-$stamp"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
$log = Join-Path $runRoot "validation.log"
$report = Join-Path $runRoot "report.json"

function Write-LogLine {
    param([string]$Text)
    Write-Host $Text
    Add-Content -LiteralPath $log -Value $Text -Encoding UTF8
}

function Invoke-NativeLogged {
    param(
        [string]$Label,
        [string[]]$Arguments
    )

    $safe = ($Label -replace '[^A-Za-z0-9_-]', '_')
    $stdoutPath = Join-Path $runRoot ($safe + ".stdout.txt")
    $stderrPath = Join-Path $runRoot ($safe + ".stderr.txt")

    Write-LogLine ""
    Write-LogLine ("== " + $Label + " ==")

    $process = Start-Process `
        -FilePath $python `
        -ArgumentList $Arguments `
        -WorkingDirectory $Repo `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath

    foreach ($path in @($stdoutPath, $stderrPath)) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            foreach ($line in @(Get-Content -LiteralPath $path)) {
                Write-LogLine $line
            }
        }
    }

    Write-LogLine ("EXIT_CODE=" + $process.ExitCode)
    return [int]$process.ExitCode
}

Write-LogLine "M115-M134 local research-brain validation"
Write-LogLine ("Windows PowerShell: " + $PSVersionTable.PSVersion.ToString())
Write-LogLine ("HEAD: " + $head)
Write-LogLine "Research only: no MT5 orders, no broker credentials, no live-write authority."

$targetedCode = Invoke-NativeLogged `
    -Label "targeted_research_brain_tests" `
    -Arguments @("-m", "unittest", "discover", "-s", "tests", "-p", "test_research_brain_m115_m134.py", "-v")

$smokeRoot = Join-Path $runRoot "runtime-smoke"
$smokeCode = Invoke-NativeLogged `
    -Label "runtime_persistence_smoke" `
    -Arguments @("tools\smoke_m115_m134.py", "--work-root", $smokeRoot)

$fullCode = $null
if ($FullSuite) {
    $fullCode = Invoke-NativeLogged `
        -Label "full_unittest_suite" `
        -Arguments @("-m", "unittest", "discover", "-s", "tests", "-v")
}

$passed = ($targetedCode -eq 0 -and $smokeCode -eq 0)
if ($FullSuite) {
    $passed = ($passed -and $fullCode -eq 0)
}

$payload = [ordered]@{
    protocol = "dusty-m115-m134-local-validation-v1"
    head = $head
    powershell = $PSVersionTable.PSVersion.ToString()
    python = $python
    targeted_tests_exit_code = $targetedCode
    runtime_smoke_exit_code = $smokeCode
    full_suite_requested = [bool]$FullSuite
    full_suite_exit_code = $fullCode
    safety = [ordered]@{
        mt5_orders = $false
        broker_credentials = $false
        broker_write = $false
        entry_veto = $false
        promotion = $false
        risk_override = $false
    }
    status = $(if ($passed) { "pass" } else { "fail" })
    log = $log
}

$payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $report -Encoding UTF8

Write-Host ""
if ($passed) {
    Write-Host "M115-M134 LOCAL WINDOWS VALIDATION PASSED"
    Write-Host "Report: $report"
    Write-Host "Log:    $log"
    exit 0
}

Write-Host "M115-M134 LOCAL WINDOWS VALIDATION FAILED"
Write-Host "Do not reinstall providers or Vibe."
Write-Host "Report: $report"
Write-Host "Log:    $log"
exit 2
