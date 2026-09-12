param(
    [string]$Repo = "C:\Users\lord1\DustyDragon-M105",
    [string]$Branch = "carson/m166-event-hypothesis-remediation",
    [Parameter(Mandatory = $true)]
    [string]$ExpectedHead
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-FalseAuthority {
    param(
        [Parameter(Mandatory = $true)]
        $Authority,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )
    foreach ($name in @("broker_write", "live_write", "custody_write", "promotion", "retry", "risk_override")) {
        if ($Authority.$name -ne $false) {
            throw "$Label authority '$name' must remain false."
        }
    }
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Exe,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )
    & $Exe @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "$Label failed with exit code $code."
    }
}

$ExpectedHead = $ExpectedHead.Trim().ToLowerInvariant()
if ($ExpectedHead -notmatch '^[0-9a-f]{40}$') {
    throw "ExpectedHead must be a full 40-character Git SHA."
}
if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    throw "Repository directory missing: $Repo"
}

$python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python virtual environment missing: $python"
}

$inputRoot = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m166-native-identity-20260912-130315"
$dataset = Join-Path $inputRoot "eurusd-m15-frozen-bars.jsonl"
$parentIdentity = Join-Path $inputRoot "m166-research-identity.json"
$parentPlan = Join-Path $inputRoot "m166-m174-provisional-research-plan.json"
$parentEstate = Join-Path $env:LOCALAPPDATA "DustyDragon\strategy-estate\reconstructions.json"
$eventRequirement = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m166-pit-event-requirement-v3\m166-event-requirement.json"
$variantRoot = Join-Path $env:LOCALAPPDATA ("DustyDragon\validation\m166-eventless-variant-v1-" + $ExpectedHead.Substring(0, 12))
$campaignRoot = Join-Path $variantRoot "provisional-quant"
$sidecarEstate = Join-Path $variantRoot "strategy-estate-eventless.json"
$variantIdentity = Join-Path $variantRoot "m166-research-identity-eventless.json"
$variantPlan = Join-Path $variantRoot "m166-m174-provisional-research-plan-eventless.json"
$remediationReceipt = Join-Path $variantRoot "m166-event-hypothesis-remediation.json"
$manifestPath = Join-Path $campaignRoot "provisional-quant-manifest.json"
$failurePath = Join-Path $campaignRoot "provisional-quant-failure.json"

foreach ($path in @($dataset, $parentIdentity, $parentPlan, $parentEstate, $eventRequirement)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required frozen input missing: $path"
    }
}

Set-Location -LiteralPath $Repo

Write-Host ""
Write-Host "=== DUSTY DRAGON M166 EVENTLESS VARIANT CAMPAIGN ===" -ForegroundColor Cyan
Write-Host "Expected head: $ExpectedHead"
Write-Host "Branch:        $Branch"

$dirty = (git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
if ($dirty) {
    Write-Host $dirty
    throw "Repository is dirty. Nothing was discarded."
}

git fetch origin "refs/heads/$Branch`:refs/remotes/origin/$Branch"
if ($LASTEXITCODE -ne 0) {
    throw "Git fetch failed."
}
$remote = (git rev-parse "refs/remotes/origin/$Branch").Trim().ToLowerInvariant()
Write-Host "Remote head:   $remote"
if ($remote -ne $ExpectedHead) {
    throw "Remote branch head differs from ExpectedHead."
}

Write-Host ""
Write-Host "--- Exact-head GitHub CI gate ---" -ForegroundColor Cyan
$uri = "https://api.github.com/repos/AnhTranHarris/Financial-AI-Learn-Maybe/commits/$ExpectedHead/check-runs?per_page=100"
$headers = @{
    "Accept" = "application/vnd.github+json"
    "User-Agent" = "DustyDragon-M166"
}
$ciReady = $false
$checks = $null
for ($poll = 1; $poll -le 120; $poll++) {
    $checks = Invoke-RestMethod -Uri $uri -Headers $headers
    if ($checks.total_count -ne 20) {
        Write-Host ("CI poll {0}: {1}/20 checks registered" -f $poll, $checks.total_count)
        Start-Sleep -Seconds 10
        continue
    }
    $failed = @(
        $checks.check_runs |
            Where-Object { $_.status -eq "completed" -and $_.conclusion -ne "success" }
    )
    if ($failed.Count -gt 0) {
        $failed | Select-Object name, status, conclusion, html_url | Format-Table -AutoSize
        throw "Exact-head CI has a real failed/cancelled check."
    }
    $unfinished = @($checks.check_runs | Where-Object { $_.status -ne "completed" })
    $success = @(
        $checks.check_runs |
            Where-Object { $_.status -eq "completed" -and $_.conclusion -eq "success" }
    ).Count
    Write-Host ("CI poll {0}: {1}/20 success; {2} unfinished" -f $poll, $success, $unfinished.Count)
    if ($unfinished.Count -eq 0 -and $success -eq 20) {
        $ciReady = $true
        break
    }
    Start-Sleep -Seconds 10
}
if (-not $ciReady) {
    if ($null -ne $checks) {
        $checks.check_runs | Select-Object name, status, conclusion | Format-Table -AutoSize
    }
    throw "Exact-head CI did not complete inside the bounded polling window."
}
Write-Host "GitHub CI: 20/20 SUCCESS" -ForegroundColor Green

git switch --detach $ExpectedHead
if ($LASTEXITCODE -ne 0) {
    throw "Unable to activate exact candidate."
}
if ((git rev-parse HEAD).Trim().ToLowerInvariant() -ne $ExpectedHead) {
    throw "HEAD mismatch after activation."
}

$env:PYTHONPATH = Join-Path $Repo "src"
New-Item -ItemType Directory -Force -Path $variantRoot | Out-Null
$transcript = Join-Path $variantRoot ("m166-eventless-campaign-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".txt")
Start-Transcript -LiteralPath $transcript -Force | Out-Null

try {
    Write-Host ""
    Write-Host "--- Focused remediation/session QC ---" -ForegroundColor Cyan
    Invoke-NativeChecked -Exe $python -Label "focused remediation QC" -Arguments @(
        "-m", "unittest",
        "tests.test_research_sessions",
        "tests.test_research_events",
        "tests.test_m166_event_requirement_inspector",
        "tests.test_m166_variant_remediation",
        "tests.test_m166_event_hypothesis_variant_builder",
        "tests.test_m166_provisional_quant",
        "tests.test_m166_provisional_quant_guarded",
        "tests.test_m166_walk_forward_lab",
        "tests.test_m167_purged_validation",
        "-v"
    )

    Write-Host ""
    Write-Host "--- M160-M169 regression tranche ---" -ForegroundColor Cyan
    Invoke-NativeChecked -Exe $python -Label "M160-M169 regression" -Arguments @(
        "-m", "unittest", "discover", "-s", "tests", "-p", "test_m16*.py", "-v"
    )

    Write-Host ""
    Write-Host "--- M170-M179 regression tranche ---" -ForegroundColor Cyan
    Invoke-NativeChecked -Exe $python -Label "M170-M179 regression" -Arguments @(
        "-m", "unittest", "discover", "-s", "tests", "-p", "test_m17*.py", "-v"
    )

    Write-Host ""
    Write-Host "--- Build immutable eventless sidecar variant ---" -ForegroundColor Cyan
    Invoke-NativeChecked -Exe $python -Label "eventless sidecar builder" -Arguments @(
        (Join-Path $Repo "tools\build_m166_event_hypothesis_variant.py"),
        "--repo", $Repo,
        "--expected-head", $ExpectedHead,
        "--dataset", $dataset,
        "--parent-identity", $parentIdentity,
        "--parent-plan", $parentPlan,
        "--event-requirement", $eventRequirement,
        "--strategy-estate", $parentEstate,
        "--output-root", $variantRoot
    )

    foreach ($path in @($sidecarEstate, $variantIdentity, $variantPlan, $remediationReceipt)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Variant builder did not create expected artifact: $path"
        }
    }

    $parentIdentityObject = Get-Content -LiteralPath $parentIdentity -Raw | ConvertFrom-Json
    $variantIdentityObject = Get-Content -LiteralPath $variantIdentity -Raw | ConvertFrom-Json
    $variantPlanObject = Get-Content -LiteralPath $variantPlan -Raw | ConvertFrom-Json
    $receiptObject = Get-Content -LiteralPath $remediationReceipt -Raw | ConvertFrom-Json

    Assert-FalseAuthority -Authority $variantIdentityObject.authority -Label "variant identity"
    Assert-FalseAuthority -Authority $variantPlanObject.authority -Label "variant plan"
    Assert-FalseAuthority -Authority $receiptObject.authority -Label "remediation receipt"

    if ($variantIdentityObject.dataset_fingerprint -ne $parentIdentityObject.dataset_fingerprint) {
        throw "Variant changed the frozen dataset fingerprint."
    }
    if ($variantIdentityObject.strategy_fingerprint -eq $parentIdentityObject.strategy_fingerprint) {
        throw "Variant strategy fingerprint did not change."
    }
    if ($variantIdentityObject.parameter_fingerprint -eq $parentIdentityObject.parameter_fingerprint) {
        throw "Variant parameter fingerprint did not change."
    }
    if ($receiptObject.variant_event_exclusion_minutes -ne 0) {
        throw "Variant event exclusion was not disabled."
    }

    New-Item -ItemType Directory -Force -Path $campaignRoot | Out-Null
    if (Test-Path -LiteralPath $failurePath -PathType Leaf) {
        $archive = Join-Path $campaignRoot ("provisional-quant-failure-prior-" + (Get-Date -Format "yyyyMMdd-HHmmssfff") + ".json")
        Move-Item -LiteralPath $failurePath -Destination $archive
        Write-Host "Archived prior failure receipt: $archive"
    }

    Write-Host ""
    Write-Host "--- Run corrected M166-M173 provisional campaign ---" -ForegroundColor Cyan
    & $python (Join-Path $Repo "tools\run_m166_m173_provisional_quant_guarded.py") `
        --repo $Repo `
        --expected-head $ExpectedHead `
        --dataset $dataset `
        --identity $variantIdentity `
        --provisional-plan $variantPlan `
        --strategy-estate $sidecarEstate `
        --output-root $campaignRoot
    $campaignCode = $LASTEXITCODE
    if ($campaignCode -ne 0) {
        if (Test-Path -LiteralPath $failurePath -PathType Leaf) {
            Write-Host ""
            Write-Host "--- Preserved campaign failure receipt ---" -ForegroundColor Yellow
            Get-Content -LiteralPath $failurePath -Raw
        }
        throw "Corrected provisional campaign failed with exit code $campaignCode."
    }

    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Campaign exited successfully but manifest is missing."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    Assert-FalseAuthority -Authority $manifest.authority -Label "provisional manifest"
    if ($manifest.production_semantics.m166_production_admission_granted -ne $false) {
        throw "Provisional campaign incorrectly granted M166 production admission."
    }
    if ($manifest.production_semantics.m174_production_certification_granted -ne $false) {
        throw "Provisional campaign incorrectly granted M174 production certification."
    }
    if ($manifest.production_semantics.m185_eligible -ne $false) {
        throw "Provisional campaign incorrectly granted M185 eligibility."
    }

    Write-Host ""
    Write-Host "=== CORRECTED PROVISIONAL CAMPAIGN COMPLETE ===" -ForegroundColor Green
    Write-Host "Runner source commit: $($manifest.runner_source_commit)"
    Write-Host "Evidence source commit: $($manifest.evidence_source_commit)"
    Write-Host "Parent strategy:       $($receiptObject.parent_strategy_fingerprint)"
    Write-Host "Variant strategy:      $($receiptObject.variant_strategy_fingerprint)"
    Write-Host "Dataset unchanged:     $($variantIdentityObject.dataset_fingerprint)"
    Write-Host "M165 observations:     $($variantPlanObject.plan.current_observation_count)"
    Write-Host "M165 distinct days:    $($variantPlanObject.plan.current_distinct_days)"
    Write-Host ""
    Write-Host "Stage status:"
    $manifest.stage_status | Format-List

    if ((git rev-parse HEAD).Trim().ToLowerInvariant() -ne $ExpectedHead) {
        throw "HEAD drift detected after campaign."
    }
    $finalDirty = (git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
    if ($finalDirty) {
        Write-Host $finalDirty
        throw "Repository became dirty."
    }

    Write-Host ""
    Write-Host "HEAD:       $ExpectedHead"
    Write-Host "TREE:       CLEAN" -ForegroundColor Green
    Write-Host "VARIANT:    $variantRoot"
    Write-Host "CAMPAIGN:   $campaignRoot"
    Write-Host "TRANSCRIPT: $transcript"
    Write-Host ""
    Write-Host "Generated files:"
    Get-ChildItem -LiteralPath $variantRoot -Recurse -File |
        Select-Object FullName, Length |
        Format-Table -AutoSize
}
finally {
    try {
        Stop-Transcript | Out-Null
    }
    catch {
    }
}
