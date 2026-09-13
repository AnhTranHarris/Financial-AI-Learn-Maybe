param(
    [string]$Repo = "C:\Users\lord1\DustyDragon-M105",
    [string]$Branch = "carson/m166-semantic-reconstruction-guard",
    [Parameter(Mandatory = $true)][string]$ExpectedHead
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
if ($ExpectedHead -notmatch '^[0-9a-f]{40}$') { throw "ExpectedHead must be a full Git SHA." }
Set-Location -LiteralPath $Repo

$dirty = (git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
if ($dirty) { Write-Host $dirty; throw "Repository is dirty. Nothing was discarded." }

git fetch origin "refs/heads/$Branch`:refs/remotes/origin/$Branch"
if ($LASTEXITCODE -ne 0) { throw "Git fetch failed." }
$remote = (git rev-parse "refs/remotes/origin/$Branch").Trim().ToLowerInvariant()
Write-Host "Expected: $ExpectedHead"
Write-Host "Remote:   $remote"
if ($remote -ne $ExpectedHead) { throw "Remote head changed. STOP." }

Write-Host ""
Write-Host "--- Exact-head GitHub CI gate ---" -ForegroundColor Cyan
$uri = "https://api.github.com/repos/AnhTranHarris/Financial-AI-Learn-Maybe/commits/$ExpectedHead/check-runs?per_page=100"
$headers = @{ "Accept" = "application/vnd.github+json"; "User-Agent" = "DustyDragon-M166-Semantic-Lineage" }
$ready = $false
for ($poll = 1; $poll -le 120; $poll++) {
    $checks = Invoke-RestMethod -Uri $uri -Headers $headers
    $runs = @($checks.check_runs)
    $failed = @($runs | Where-Object { $_.status -eq "completed" -and $_.conclusion -ne "success" })
    if ($failed.Count -gt 0) {
        $failed | Select-Object name,status,conclusion,html_url | Format-Table -AutoSize
        throw "Exact-head CI failed."
    }
    $success = @($runs | Where-Object { $_.status -eq "completed" -and $_.conclusion -eq "success" }).Count
    $unfinished = @($runs | Where-Object { $_.status -ne "completed" }).Count
    Write-Host ("CI poll {0}: {1}/20 success; {2} unfinished" -f $poll,$success,$unfinished)
    if ($checks.total_count -eq 20 -and $success -eq 20 -and $unfinished -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 10
}
if (-not $ready) { throw "Exact-head CI did not complete inside bounded polling window." }
Write-Host "GitHub CI: 20/20 SUCCESS" -ForegroundColor Green

git switch --detach $ExpectedHead
if ($LASTEXITCODE -ne 0) { throw "Unable to activate exact head." }
if ((git rev-parse HEAD).Trim().ToLowerInvariant() -ne $ExpectedHead) { throw "HEAD mismatch." }

$python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python venv missing." }
$env:PYTHONPATH = Join-Path $Repo "src"

Write-Host ""
Write-Host "--- Reconstruction semantic contract QC ---" -ForegroundColor Cyan
Invoke-NativeChecked -Exe $python -Label "semantic contract QC" -Arguments @(
    "-m","unittest",
    "tests.test_reconstruction_feature_contract",
    "tests.test_reconstruction_retirement",
    "tests.test_reconstruction_semantics",
    "tests.test_m166_semantic_remediation",
    "tests.test_m1965_ollama_strategy_reconstruction",
    "tests.test_m1966_strategy_reconstruction_campaign",
    "-v"
)

Write-Host ""
Write-Host "--- M160-M179 regression QC ---" -ForegroundColor Cyan
Invoke-NativeChecked -Exe $python -Label "M160-M179 regression" -Arguments @(
    "-m","unittest","discover","-s","tests","-p","test_m1*.py","-v"
)

$childRoot = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m166-semantic-child-v1-b5d55212ea59"
$childAudit = Join-Path $childRoot "m166-semantic-child-audit.json"
$childReceipt = Join-Path $childRoot "m166-semantic-child-receipt.json"
$estate = Join-Path $env:LOCALAPPDATA "DustyDragon\strategy-estate\reconstructions.json"
$outputRoot = Join-Path $env:LOCALAPPDATA ("DustyDragon\validation\m166-semantic-lineage-final-v1-" + $ExpectedHead.Substring(0,12))
$output = Join-Path $outputRoot "m166-semantic-lineage-retirement.json"
foreach ($path in @($childAudit,$childReceipt,$estate)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required evidence missing: $path" }
}

Write-Host ""
Write-Host "--- Retire dead reconstruction and audit Strategy Estate ---" -ForegroundColor Cyan
Invoke-NativeChecked -Exe $python -Label "semantic lineage finalization" -Arguments @(
    (Join-Path $Repo "tools\finalize_m166_semantic_lineage.py"),
    "--child-audit",$childAudit,
    "--child-receipt",$childReceipt,
    "--strategy-estate",$estate,
    "--output",$output
)

$finalDirty = (git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
if ($finalDirty) { Write-Host $finalDirty; throw "QC changed repository working tree. STOP." }
if ((git rev-parse HEAD).Trim().ToLowerInvariant() -ne $ExpectedHead) { throw "HEAD drift after QC." }

Write-Host ""
Write-Host "=== M166 SEMANTIC LINEAGE FINALIZATION COMPLETE ===" -ForegroundColor Green
Write-Host "HEAD:   $ExpectedHead"
Write-Host "TREE:   CLEAN"
Write-Host "OUTPUT: $output"
Write-Host ""
Write-Host "Please send Carson the complete PowerShell output and m166-semantic-lineage-retirement.json"
