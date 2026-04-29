Param(
    [string]$CommitMessage = "",
    [string]$Branch = "",
    [switch]$SkipPullRebase,
    [switch]$SkipPush,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Resolve-GitExecutable {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        return $git.Source
    }

    $fallbackGit = "C:\Program Files\Microsoft Visual Studio\18\Insiders\Common7\IDE\CommonExtensions\Microsoft\TeamFoundation\Team Explorer\Git\mingw64\bin\git.exe"
    if (Test-Path $fallbackGit) {
        $fallbackCa = "C:\Program Files\Microsoft Visual Studio\18\Insiders\Common7\IDE\CommonExtensions\Microsoft\TeamFoundation\Team Explorer\Git\mingw64\etc\ssl\certs\ca-bundle.crt"
        if (Test-Path $fallbackCa) {
            $env:GIT_SSL_CAINFO = $fallbackCa
        }
        return $fallbackGit
    }

    throw "Git executable not found on PATH and Visual Studio fallback path was not found."
}

function Normalize-Lines {
    param([object]$Output)

    if ($null -eq $Output) {
        return @()
    }
    if ($Output -is [string]) {
        return @($Output)
    }
    return @($Output)
}

function Invoke-Git {
    param(
        [string[]]$GitArgs,
        [switch]$AllowFail,
        [switch]$WriteOperation
    )

    $commandText = "git " + ($GitArgs -join " ")
    if ($DryRun -and $WriteOperation) {
        Write-Host "[dry-run] $commandText"
        return @{ ExitCode = 0; Output = @() }
    }

    Write-Host $commandText
    $output = & $script:GitExe @GitArgs 2>&1
    $exitCode = $LASTEXITCODE

    $lines = Normalize-Lines $output
    foreach ($line in $lines) {
        if ($line) {
            Write-Host $line
        }
    }

    if (-not $AllowFail -and $exitCode -ne 0) {
        throw "Command failed (exit $exitCode): $commandText"
    }

    return @{ ExitCode = $exitCode; Output = $lines }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$script:GitExe = Resolve-GitExecutable

$insideRepo = Invoke-Git -GitArgs @("rev-parse", "--is-inside-work-tree")
if (($insideRepo.Output | Select-Object -Last 1).Trim() -ne "true") {
    throw "Current path is not a Git repository: $repoRoot"
}

$currentBranchResult = Invoke-Git -GitArgs @("rev-parse", "--abbrev-ref", "HEAD")
$currentBranch = ($currentBranchResult.Output | Select-Object -Last 1).Trim()
if ($currentBranch -eq "HEAD") {
    throw "Detached HEAD detected. Checkout a branch first, or pass -Branch."
}

$targetBranch = if ($Branch) { $Branch } else { $currentBranch }

$upstreamCheck = Invoke-Git -GitArgs @("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") -AllowFail
$hasUpstream = $upstreamCheck.ExitCode -eq 0

$statusResult = Invoke-Git -GitArgs @("status", "--porcelain")
$statusLines = @($statusResult.Output | Where-Object { $_ -and $_.Trim().Length -gt 0 })
$hasChanges = $statusLines.Count -gt 0

if ($hasChanges) {
    Invoke-Git -GitArgs @("add", "-A") -WriteOperation | Out-Null

    $effectiveMessage = if ($CommitMessage) {
        $CommitMessage
    }
    else {
        "chore: daily sync " + (Get-Date -Format "yyyy-MM-dd HH:mm")
    }

    Invoke-Git -GitArgs @("commit", "-m", $effectiveMessage) -WriteOperation | Out-Null
}
else {
    Write-Host "No local file changes detected."
}

if (-not $SkipPush) {
    if (-not $SkipPullRebase -and $hasUpstream) {
        Invoke-Git -GitArgs @("pull", "--rebase", "--autostash") -WriteOperation | Out-Null
    }

    if ($hasUpstream) {
        Invoke-Git -GitArgs @("push") -WriteOperation | Out-Null
    }
    else {
        Invoke-Git -GitArgs @("push", "-u", "origin", $targetBranch) -WriteOperation | Out-Null
    }
}
else {
    Write-Host "SkipPush enabled. Commit complete; push was skipped."
}

$headResult = Invoke-Git -GitArgs @("rev-parse", "HEAD")
$headSha = ($headResult.Output | Select-Object -Last 1).Trim()

Write-Host "Done."
Write-Host "Branch: $targetBranch"
Write-Host "HEAD:   $headSha"