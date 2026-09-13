param(
    [string]$Repo = "C:\Users\lord1\DustyDragon-M105",
    [string]$Branch = "carson/m1967-normalized-reconstruction-canary",
    [Parameter(Mandatory=$true)][string]$ExpectedHead
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-NativeChecked {
    param([string]$Exe, [string[]]$Arguments, [string]$Label)
    & $Exe @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "$Label failed with exit code $code." }
}

function Get-FileSha256OrMissing {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "MISSING" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

$ExpectedHead = $ExpectedHead.Trim().ToLowerInvariant()
if ($ExpectedHead -notmatch '^[0-9a-f]{40}$') { throw "ExpectedHead must be a full 40-character Git SHA." }

$python = Join-Path $Repo ".venv\Scripts\python.exe"
$dataset = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m166-native-identity-20260912-130315\eurusd-m15-frozen-bars.jsonl"
$mainEstate = Join-Path $env:LOCALAPPDATA "DustyDragon\strategy-estate\reconstructions.json"
$outputRoot = Join-Path $env:LOCALAPPDATA ("DustyDragon\validation\m1968-normalized-canary-v1-" + $ExpectedHead.Substring(0,12))
$sidecarEstate = Join-Path $outputRoot "strategy-estate-normalized-canary.json"
$receipt = Join-Path $outputRoot "m1968-normalized-canary-receipt.json"

foreach ($path in @($python, $dataset)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required input missing: $path" }
}

Set-Location -LiteralPath $Repo
$dirty = (git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
if ($dirty) { Write-Host $dirty; throw "Repository is dirty. Nothing will be discarded." }

git fetch origin "refs/heads/$Branch`:refs/remotes/origin/$Branch"
if ($LASTEXITCODE -ne 0) { throw "Git fetch failed." }
$remote = (git rev-parse "refs/remotes/origin/$Branch").Trim().ToLowerInvariant()
Write-Host "Expected: $ExpectedHead"
Write-Host "Remote:   $remote"
if ($remote -ne $ExpectedHead) { throw "Remote branch head differs from ExpectedHead. STOP." }

Write-Host ""
Write-Host "--- Exact-head GitHub CI gate ---" -ForegroundColor Cyan
$headers = @{ "Accept"="application/vnd.github+json"; "User-Agent"="DustyDragon-M1968-Canary" }
$uri = "https://api.github.com/repos/AnhTranHarris/Financial-AI-Learn-Maybe/commits/$ExpectedHead/check-runs?per_page=100"
$ready = $false
for ($poll=1; $poll -le 120; $poll++) {
    $checks = Invoke-RestMethod -Uri $uri -Headers $headers
    if ($checks.total_count -ne 20) {
        Write-Host ("CI poll {0}: {1}/20 checks registered" -f $poll, $checks.total_count)
        Start-Sleep -Seconds 10
        continue
    }
    $failed = @($checks.check_runs | Where-Object { $_.status -eq "completed" -and $_.conclusion -ne "success" })
    if ($failed.Count -gt 0) {
        $failed | Select-Object name,status,conclusion | Format-Table -AutoSize
        throw "Exact-head CI failed."
    }
    $unfinished = @($checks.check_runs | Where-Object { $_.status -ne "completed" })
    $success = @($checks.check_runs | Where-Object { $_.status -eq "completed" -and $_.conclusion -eq "success" }).Count
    Write-Host ("CI poll {0}: {1}/20 success; {2} unfinished" -f $poll,$success,$unfinished.Count)
    if ($success -eq 20 -and $unfinished.Count -eq 0) { $ready=$true; break }
    Start-Sleep -Seconds 10
}
if (-not $ready) { throw "Exact-head CI did not complete inside bounded polling window." }
Write-Host "GitHub CI: 20/20 SUCCESS" -ForegroundColor Green

git switch --detach $ExpectedHead
if ($LASTEXITCODE -ne 0) { throw "Unable to activate exact candidate." }
$current = (git rev-parse HEAD).Trim().ToLowerInvariant()
if ($current -ne $ExpectedHead) { throw "HEAD mismatch. STOP." }

$env:PYTHONPATH = Join-Path $Repo "src"

Write-Host ""
Write-Host "--- Focused normalized reconstruction QC ---" -ForegroundColor Cyan
Invoke-NativeChecked -Exe $python -Label "focused normalized reconstruction QC" -Arguments @(
    "-m","unittest",
    "tests.test_reconstruction_feature_contract",
    "tests.test_reconstruction_runtime_features",
    "tests.test_ollama_semantic_strategy_reconstruction",
    "tests.test_reconstruction_semantics",
    "-v"
)

Write-Host ""
Write-Host "--- M160-M169 regression tranche ---" -ForegroundColor Cyan
Invoke-NativeChecked -Exe $python -Label "M160-M169 regression" -Arguments @(
    "-m","unittest","discover","-s","tests","-p","test_m16*.py","-v"
)

Write-Host ""
Write-Host "--- M170-M179 regression tranche ---" -ForegroundColor Cyan
Invoke-NativeChecked -Exe $python -Label "M170-M179 regression" -Arguments @(
    "-m","unittest","discover","-s","tests","-p","test_m17*.py","-v"
)

Write-Host ""
Write-Host "--- Full regression suite ---" -ForegroundColor Cyan
Invoke-NativeChecked -Exe $python -Label "full regression suite" -Arguments @(
    "-m","unittest","discover","-s","tests","-p","test_*.py","-v"
)

$mainBefore = Get-FileSha256OrMissing -Path $mainEstate
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

Write-Host ""
Write-Host "--- Normalized EURUSD reconstruction canary ---" -ForegroundColor Cyan
& $python (Join-Path $Repo "tools\run_m1968_normalized_canary.py") `
    "--dataset" $dataset `
    "--estate" $sidecarEstate `
    "--receipt" $receipt
$canaryExit = $LASTEXITCODE
if ($canaryExit -notin @(0,3,4)) { throw "Normalized canary failed with unexpected exit code $canaryExit." }
if (-not (Test-Path -LiteralPath $receipt -PathType Leaf)) { throw "Canary receipt missing: $receipt" }

$mainAfter = Get-FileSha256OrMissing -Path $mainEstate
if ($mainBefore -ne $mainAfter) { throw "Main Strategy Estate changed during sidecar canary. STOP." }

$receiptObject = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
if ([string]$receiptObject.protocol -ne "dusty-m1968-normalized-reconstruction-canary-v1") { throw "Canary receipt protocol mismatch." }
if ($receiptObject.main_strategy_estate_modified -eq $true) { throw "Canary claims main Strategy Estate mutation. STOP." }
if ($receiptObject.threshold_tuning_performed -eq $true) { throw "Threshold tuning must remain false." }
foreach ($name in @("broker_write","live_write","custody_write","promotion","retry","risk_override","guardian_override")) {
    if ($receiptObject.authority.$name -ne $false) { throw "Authority must remain false: $name" }
}

$tree = (git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
if ($tree) { Write-Host $tree; throw "Repository became dirty during certification." }

Write-Host ""
Write-Host "=== M196.8 NORMALIZED CANARY PREFLIGHT COMPLETE ===" -ForegroundColor Green
Write-Host "HEAD:        $ExpectedHead"
Write-Host "TREE:        CLEAN"
Write-Host "MAIN ESTATE: UNCHANGED ($mainAfter)"
Write-Host "STATUS:      $($receiptObject.status)"
if ($null -ne $receiptObject.semantic_assessment) {
    Write-Host "SEMANTIC:    $($receiptObject.semantic_assessment.status)"
    Write-Host "ENTRY MATCH: $($receiptObject.semantic_assessment.entry_match_count)"
    Write-Host "REASON:      $($receiptObject.semantic_assessment.reason)"
}
Write-Host "CANARY EXIT: $canaryExit"
Write-Host "OUTPUT ROOT: $outputRoot"
Write-Host ""
if ($canaryExit -eq 0) {
    Write-Host "Canary is semantically activatable. Send Carson the artifacts below." -ForegroundColor Green
} elseif ($canaryExit -eq 4) {
    Write-Host "Canary produced a valid research rejection. Evidence is preserved; do not tune/retry." -ForegroundColor Yellow
} else {
    Write-Host "Ollama/reconstruction was unavailable. Evidence is preserved; do not blind-retry." -ForegroundColor Yellow
}
Write-Host "Please send Carson:"
Write-Host "  1. Complete PowerShell output"
Write-Host "  2. m1968-normalized-canary-receipt.json"
Write-Host "  3. strategy-estate-normalized-canary.json (if created)"
