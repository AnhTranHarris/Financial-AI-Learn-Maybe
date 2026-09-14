param(
    [string]$Repo = "C:\Users\lord1\DustyDragon-M105",
    [string]$Branch = "carson/m19683-safe-provider-evidence",
    [Parameter(Mandatory = $true)][string]$ExpectedHead
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-LastExit([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

Set-Location -LiteralPath $Repo

$dirty = (git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
if ($dirty) { Write-Host $dirty; throw "Repository is dirty. Nothing will be discarded." }

$current = (git rev-parse HEAD).Trim().ToLowerInvariant()
if ($current -ne $ExpectedHead.ToLowerInvariant()) { throw "Exact HEAD mismatch before M196.8.3." }

$remote = (git ls-remote origin "refs/heads/$Branch" | ForEach-Object { ($_ -split "`t")[0] } | Out-String).Trim().ToLowerInvariant()
if (-not $remote -or $remote -ne $ExpectedHead.ToLowerInvariant()) { throw "GitHub branch does not equal ExpectedHead." }

Write-Host "=== M196.8.3 EXACT-HEAD CI GATE ==="
$checksUrl = "https://api.github.com/repos/AnhTranHarris/Financial-AI-Learn-Maybe/commits/$ExpectedHead/check-runs?per_page=100"
$attempt = 0
while ($true) {
    $attempt++
    $headers = @{ "Accept" = "application/vnd.github+json"; "User-Agent" = "DustyDragon-M19683" }
    $response = Invoke-RestMethod -Method Get -Uri $checksUrl -Headers $headers -TimeoutSec 20
    $runs = @($response.check_runs)
    $pending = @($runs | Where-Object { $_.status -ne "completed" })
    $failed = @($runs | Where-Object { $_.status -eq "completed" -and $_.conclusion -ne "success" })
    Write-Host ("CI attempt {0}: total={1} pending={2} failed={3}" -f $attempt, $runs.Count, $pending.Count, $failed.Count)
    if ($failed.Count -gt 0) {
        $failed | ForEach-Object { Write-Host ("FAILED: {0} => {1}" -f $_.name, $_.conclusion) }
        throw "Exact-head CI failure."
    }
    if ($runs.Count -eq 20 -and $pending.Count -eq 0) { break }
    if ($attempt -ge 120) { throw "Exact-head CI did not reach 20/20 success inside bounded polling window." }
    Start-Sleep -Seconds 10
}
Write-Host "CI: 20/20 SUCCESS"

$python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python venv missing." }
$env:PYTHONPATH = (Join-Path $Repo "src")

Write-Host "=== FOCUSED QC ==="
& $python -m unittest -v tests.test_m19683_safe_provider_evidence tests.test_m19681_ollama_reconstruction_diagnostic
Assert-LastExit "Focused M196.8.3 QC failed."

$prior = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m19681-ollama-diagnostic-v1-de1c4ec8de6f\m19681-ollama-reconstruction-diagnostic.json"
if (-not (Test-Path -LiteralPath $prior -PathType Leaf)) { throw "Prior M196.8.1 diagnostic evidence missing." }

$head12 = $ExpectedHead.Substring(0, 12)
$outputRoot = Join-Path $env:LOCALAPPDATA "DustyDragon\validation\m19683-safe-provider-evidence-v1-$head12"
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$output = Join-Path $outputRoot "m19683-safe-provider-evidence.json"

Write-Host "=== SAFE OLLAMA PROVIDER EVIDENCE ==="
Write-Host "No Ollama CLI command and no generation request will be executed."
& $python (Join-Path $Repo "tools\run_m19683_safe_provider_evidence.py") --prior-diagnostic $prior --output $output
Assert-LastExit "M196.8.3 safe provider evidence failed."

$currentAfter = (git rev-parse HEAD).Trim().ToLowerInvariant()
$dirtyAfter = (git status --porcelain=v1 --untracked-files=all | Out-String).Trim()
if ($currentAfter -ne $ExpectedHead.ToLowerInvariant()) { throw "HEAD drift after M196.8.3." }
if ($dirtyAfter) { Write-Host $dirtyAfter; throw "Repository became dirty during M196.8.3." }

Write-Host ""
Write-Host "=== M196.8.3 SAFE PROVIDER EVIDENCE COMPLETE ==="
Write-Host "HEAD:   $currentAfter"
Write-Host "TREE:   CLEAN"
Write-Host "OUTPUT: $output"
Write-Host "No Ollama CLI invocation, model generation, or canary retry was performed."
