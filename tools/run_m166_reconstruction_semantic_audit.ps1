param(
    [string]$Repo = "C:\Users\lord1\DustyDragon-M105",
    [string]$Branch = "carson/m166-semantic-reconstruction-guard",
    [Parameter(Mandatory=$true)][string]$ExpectedHead
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$python = Join-Path $Repo ".venv\Scripts\python.exe"
$dataset = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m166-native-identity-20260912-130315\eurusd-m15-frozen-bars.jsonl"
$estate = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m166-eventless-variant-v1-66f61d07d0db\strategy-estate-eventless.json"
$strategy = "3330cd5e5d948f79cd7b51f2e2e7f80306071d60e24ce6936570ad304e8ec257"
$outputRoot = Join-Path $env:LOCALAPPDATA ("DustyDragon\validation\m166-semantic-audit-v1-" + $ExpectedHead.Substring(0,12))
$output = Join-Path $outputRoot "m166-reconstruction-semantic-audit.json"

Set-Location -LiteralPath $Repo

$dirty = (git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
if ($dirty) {
    Write-Host $dirty
    throw "Repository is dirty. Nothing will be discarded."
}

foreach ($path in @($python, $dataset, $estate)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required input missing: $path"
    }
}

git fetch origin "refs/heads/$Branch`:refs/remotes/origin/$Branch"
if ($LASTEXITCODE -ne 0) { throw "Git fetch failed." }

$remote = (git rev-parse "refs/remotes/origin/$Branch").Trim()
Write-Host "Expected: $ExpectedHead"
Write-Host "Remote:   $remote"
if ($remote -ne $ExpectedHead) { throw "Remote head changed. STOP." }

$headers = @{
    "Accept" = "application/vnd.github+json"
    "User-Agent" = "DustyDragon-M166-Semantic-QC"
}
$checksUri = "https://api.github.com/repos/AnhTranHarris/Financial-AI-Learn-Maybe/commits/$ExpectedHead/check-runs?per_page=100"
$checks = Invoke-RestMethod -Uri $checksUri -Headers $headers -Method Get
if ($checks.total_count -ne 20) { throw "Expected exactly 20 exact-head GitHub checks." }
$unfinished = @($checks.check_runs | Where-Object { $_.status -ne "completed" })
$bad = @($checks.check_runs | Where-Object { $_.status -eq "completed" -and $_.conclusion -ne "success" })
if ($unfinished.Count -gt 0) { throw "Exact-head CI is still running/queued. STOP." }
if ($bad.Count -gt 0) {
    $bad | Select-Object name,status,conclusion | Format-Table -AutoSize
    throw "Exact-head CI failed. STOP."
}
Write-Host "GitHub CI: 20/20 SUCCESS" -ForegroundColor Green

git switch --detach $ExpectedHead
if ($LASTEXITCODE -ne 0) { throw "Unable to activate exact candidate." }
if ((git rev-parse HEAD).Trim() -ne $ExpectedHead) { throw "HEAD mismatch." }

$env:PYTHONPATH = Join-Path $Repo "src"

Write-Host ""
Write-Host "--- Semantic preflight unit QC ---" -ForegroundColor Cyan
& $python -m unittest tests.test_reconstruction_semantics -v
if ($LASTEXITCODE -ne 0) { throw "Semantic unit QC failed." }

Write-Host ""
Write-Host "--- M166 reconstruction semantic audit ---" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
& $python ".\tools\audit_m166_reconstruction_semantics.py" `
    --dataset $dataset `
    --strategy-estate $estate `
    --strategy-fingerprint $strategy `
    --training-days 365 `
    --output $output
if ($LASTEXITCODE -ne 0) { throw "Semantic audit failed." }

$finalDirty = (git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
if ($finalDirty) {
    Write-Host $finalDirty
    throw "Repository became dirty. STOP."
}
if ((git rev-parse HEAD).Trim() -ne $ExpectedHead) { throw "Final HEAD drift." }

Write-Host ""
Write-Host "=== M166 SEMANTIC AUDIT COMPLETE ===" -ForegroundColor Green
Write-Host "HEAD:   $ExpectedHead"
Write-Host "TREE:   CLEAN"
Write-Host "OUTPUT: $output"
Write-Host ""
Write-Host "Please send Carson:"
Write-Host "  1. Complete PowerShell output"
Write-Host "  2. m166-reconstruction-semantic-audit.json"
