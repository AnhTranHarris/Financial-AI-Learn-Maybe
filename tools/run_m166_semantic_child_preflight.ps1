param(
    [string]$Repo = "C:\Users\lord1\DustyDragon-M105",
    [string]$Branch = "carson/m166-semantic-reconstruction-guard",
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

$ExpectedHead = $ExpectedHead.Trim().ToLowerInvariant()
if ($ExpectedHead -notmatch '^[0-9a-f]{40}$') { throw "ExpectedHead must be a full 40-character Git SHA." }

$python = Join-Path $Repo ".venv\Scripts\python.exe"
$dataset = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m166-native-identity-20260912-130315\eurusd-m15-frozen-bars.jsonl"
$parentEstate = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m166-eventless-variant-v1-66f61d07d0db\strategy-estate-eventless.json"
$parentAudit = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m166-semantic-audit-v1-8ccab92b9e27\m166-reconstruction-semantic-audit.json"
$outputRoot = Join-Path $env:LOCALAPPDATA ("DustyDragon\validation\m166-semantic-child-v1-" + $ExpectedHead.Substring(0,12))
$childEstate = Join-Path $outputRoot "strategy-estate-semantic-child.json"
$receipt = Join-Path $outputRoot "m166-semantic-child-receipt.json"
$childAudit = Join-Path $outputRoot "m166-semantic-child-audit.json"

foreach ($path in @($python, $dataset, $parentEstate, $parentAudit)) {
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
if ($remote -ne $ExpectedHead) { throw "Remote branch head differs from ExpectedHead." }

Write-Host ""
Write-Host "--- Exact-head GitHub CI gate ---" -ForegroundColor Cyan
$headers = @{ "Accept"="application/vnd.github+json"; "User-Agent"="DustyDragon-M166-Semantic-Child" }
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
if ((git rev-parse HEAD).Trim().ToLowerInvariant() -ne $ExpectedHead) { throw "HEAD mismatch." }

$env:PYTHONPATH = Join-Path $Repo "src"

Write-Host ""
Write-Host "--- Focused semantic QC ---" -ForegroundColor Cyan
Invoke-NativeChecked -Exe $python -Label "focused semantic QC" -Arguments @(
    "-m","unittest",
    "tests.test_reconstruction_semantics",
    "tests.test_m166_semantic_remediation",
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

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

Write-Host ""
Write-Host "--- Build immutable semantic child ---" -ForegroundColor Cyan
Invoke-NativeChecked -Exe $python -Label "semantic child builder" -Arguments @(
    (Join-Path $Repo "tools\build_m166_semantic_child.py"),
    "--strategy-estate",$parentEstate,
    "--semantic-audit",$parentAudit,
    "--output-root",$outputRoot
)

foreach ($path in @($childEstate,$receipt)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Builder did not create expected artifact: $path" }
}
$receiptObject = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
$childStrategy = [string]$receiptObject.child_strategy_fingerprint
if ($childStrategy -notmatch '^[0-9a-f]{64}$') { throw "Child strategy fingerprint invalid." }
if ($receiptObject.parent_preserved -ne $true) { throw "Parent preservation not certified." }
if ($receiptObject.threshold_tuning_performed -ne $false) { throw "Threshold tuning must remain false." }

Write-Host ""
Write-Host "--- Semantic preflight of immutable child ---" -ForegroundColor Cyan
Invoke-NativeChecked -Exe $python -Label "semantic child audit" -Arguments @(
    (Join-Path $Repo "tools\audit_m166_reconstruction_semantics.py"),
    "--dataset",$dataset,
    "--strategy-estate",$childEstate,
    "--strategy-fingerprint",$childStrategy,
    "--training-days","365",
    "--output",$childAudit
)

$auditObject = Get-Content -LiteralPath $childAudit -Raw | ConvertFrom-Json
Write-Host ""
Write-Host "=== M166 SEMANTIC CHILD PREFLIGHT COMPLETE ===" -ForegroundColor Green
Write-Host "HEAD:        $ExpectedHead"
Write-Host "TREE:        $((git status --porcelain=v1 --untracked-files=all | Out-String).Trim() -eq '')"
Write-Host "CHILD:       $childStrategy"
Write-Host "STATUS:      $($auditObject.assessment.status)"
Write-Host "ENTRY MATCH: $($auditObject.assessment.entry_match_count)"
Write-Host "REASON:      $($auditObject.assessment.reason)"
Write-Host "OUTPUT ROOT: $outputRoot"
Write-Host ""
Write-Host "Please send Carson:"
Write-Host "  1. Complete PowerShell output"
Write-Host "  2. m166-semantic-child-receipt.json"
Write-Host "  3. m166-semantic-child-audit.json"
