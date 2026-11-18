# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

<#
.SYNOPSIS
Backs up the editor's dataset to the "dataset" branch of the private GitHub repository.

.DESCRIPTION
The Windows twin of cloud/backup_dataset.sh (which is the tested one; keep them in step).
Text only: chat, tool calls, manual edits, feedback (see cloud\DATASET.md). No media, models
or projects. Commits only when something changed. Refuses a public repository (when the gh
CLI can tell) and stops if anything in the dataset looks like a credential.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File cloud\pc\backup_dataset.ps1 -DataDir D:\edit
#>

param(
    [string]$DataDir = "$env:USERPROFILE\latest-avenue-edit-data",
    [string]$Url = "",
    [string]$Branch = "dataset"
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Source = Join-Path $DataDir "dataset"
$Clone = Join-Path $DataDir "dataset-backup"

function Fail($text) { Write-Host "Stopped: $text" -ForegroundColor Red; exit 1 }
function Quiet([scriptblock]$block) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $block *> $null } catch { }
    $ErrorActionPreference = $previous
}

if (-not (Test-Path $Source)) { Write-Host "Nothing to back up yet: $Source does not exist."; exit 0 }
if (-not $Url) { $Url = (git -C $Repo remote get-url origin) }
if (-not $Url) { Fail "no repository address. Pass -Url." }

# Never publish it: the dataset holds what was asked and done, which is private.
# Asked with no sign-in: GitHub shows a private repository to nobody (404) and a public one to
# everybody (200). This works without the gh CLI, which most PCs don't have. If GitHub can't
# be asked, stop rather than guess.
if ($Url -match 'github\.com') {
    $slug = ($Url -replace '^.*github\.com[:/]', '') -replace '\.git$', ''
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $visible = $null
    try {
        $info = Invoke-RestMethod -UseBasicParsing -TimeoutSec 20 -Headers @{ "User-Agent" = "latest-avenue-dataset-backup" } "https://api.github.com/repos/$slug"
        $visible = -not $info.private
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        if ($status -eq 404) { $visible = $false }
    }
    if ($null -eq $visible) { Fail "could not ask GitHub whether $slug is private (offline or rate-limited). Nothing was backed up." }
    if ($visible) { Fail "$slug is a public repository. The dataset only goes to a private one." }
}

# Anything that looks like a credential stops the backup (a second lock: the server never
# writes tokens to the dataset).
$pattern = 'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abprs]-[A-Za-z0-9-]{20,}'
$files = Get-ChildItem -Path $Source -Recurse -File | Where-Object { $_.Extension -in ".jsonl", ".json" }
$hits = $files | Select-String -Pattern $pattern -List
if ($hits) {
    Write-Host "These files contain something that looks like a credential:"
    $hits | ForEach-Object { Write-Host "  $($_.Path)" }
    Fail "nothing was backed up."
}

if (-not (Test-Path (Join-Path $Clone ".git"))) {
    if (Test-Path $Clone) { Remove-Item -Recurse -Force $Clone }
    $env:GIT_LFS_SKIP_SMUDGE = "1"
    Quiet { git ls-remote --exit-code --heads $Url $Branch }
    if ($LASTEXITCODE -eq 0) {
        git clone --quiet --single-branch --branch $Branch $Url $Clone
        if ($LASTEXITCODE -ne 0) { Fail "could not clone the $Branch branch." }
    } else {
        git init --quiet -b $Branch $Clone
        git -C $Clone remote add origin $Url
    }
}
if (-not (git -C $Clone config user.name)) { git -C $Clone config user.name "latest avenue video" }
if (-not (git -C $Clone config user.email)) { git -C $Clone config user.email "dataset@latestavenue.invalid" }
Quiet { git -C $Clone pull --quiet --ff-only origin $Branch }

# Mirror the dataset into the clone (text files only; the clone's .git and README stay).
Get-ChildItem -Path $Clone -Force | Where-Object { $_.Name -notin ".git", "README.md" } |
    Remove-Item -Recurse -Force
foreach ($file in $files) {
    $relative = $file.FullName.Substring($Source.Length).TrimStart("\")
    $target = Join-Path $Clone $relative
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    Copy-Item $file.FullName $target -Force
}
$readme = Join-Path $Clone "README.md"
if (-not (Test-Path $readme)) { Copy-Item (Join-Path $Repo "cloud\DATASET.md") $readme }

git -C $Clone add -A
git -C $Clone diff --cached --quiet
if ($LASTEXITCODE -eq 0) { Write-Host "Dataset backup: nothing new."; exit 0 }
$count = @(git -C $Clone diff --cached --numstat).Count
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mmZ")
git -C $Clone commit --quiet -m "Dataset backup $stamp ($count files changed)"
if ($LASTEXITCODE -ne 0) { Fail "could not commit." }
git -C $Clone push --quiet origin $Branch
if ($LASTEXITCODE -ne 0) { Fail "could not push to $Branch." }
Write-Host "Dataset backup: pushed $count changed files to $Branch."
