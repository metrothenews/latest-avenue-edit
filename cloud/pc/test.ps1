# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

<#
.SYNOPSIS
Builds the editor's container and runs every test in it, on a generated 12x12 pixel shoot.

.DESCRIPTION
Leaves the running editor alone: the images are tagged latest-avenue-edit:candidate and
latest-avenue-edit:test (the live one is latest-avenue-edit:local), the test container has no
name, no ports and is removed when it ends. The speech model (about 140 MB, more for the GPU
model) is kept in the Docker volume "la-edit-test-models" between runs.

.PARAMETER Gpu
Build with the CUDA libraries and run with the NVIDIA card (falls back to the processor if
Docker can't give the card to the container).

.PARAMETER NoBuild
Run the tests on the images already built.

.PARAMETER AppTarball
latest-avenue-video-linux-x64.tar.xz to use; by default it is fetched from the repository's
"linux-build" branch into the repository folder.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File cloud\pc\test.ps1 -Gpu
#>

param([switch]$Gpu, [switch]$NoBuild, [string]$AppTarball = "")

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$File = "latest-avenue-video-linux-x64.tar.xz"
function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor White }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed. Install Docker Desktop (WSL 2), start it, and run this again."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Start it and run this again." }

if (-not $NoBuild) {
    Step "The editor's Linux build"
    $target = Join-Path $Repo $File
    if ($AppTarball) { Copy-Item $AppTarball $target -Force }
    if (-not (Test-Path $target)) {
        $temp = Join-Path ([IO.Path]::GetTempPath()) ("la-build-" + [guid]::NewGuid().ToString("N"))
        $env:GIT_LFS_SKIP_SMUDGE = "1"
        $url = (git -C $Repo remote get-url origin)
        git clone --depth 1 --single-branch --branch linux-build $url $temp
        if ($LASTEXITCODE -ne 0) { throw "Could not fetch the linux-build branch from $url" }
        $out = [IO.File]::Create($target)
        try {
            foreach ($part in (Get-ChildItem $temp -Filter "$File.part*" | Sort-Object Name)) {
                $in = [IO.File]::OpenRead($part.FullName)
                try { $in.CopyTo($out) } finally { $in.Dispose() }
            }
        } finally { $out.Dispose() }
        $want = ((Get-Content (Join-Path $temp "SHA256SUMS")) -split "\s+")[0].ToLower()
        $have = (Get-FileHash $target -Algorithm SHA256).Hash.ToLower()
        Remove-Item -Recurse -Force $temp
        if ($want -ne $have) { Remove-Item $target; throw "The build file's checksum doesn't match." }
    }
    Write-Host "Using $target"

    Step "Building the editor image (first time: 10-20 minutes)"
    $cuda = if ($Gpu) { "1" } else { "0" }
    docker build --build-arg "ASSEMBLY_CUDA=$cuda" -f (Join-Path $Repo "cloud\Dockerfile") -t latest-avenue-edit:candidate $Repo
    if ($LASTEXITCODE -ne 0) { throw "Building the editor image failed." }

    Step "Building the test image"
    docker build --build-arg BASE=latest-avenue-edit:candidate -f (Join-Path $Repo "cloud\Dockerfile.test") -t latest-avenue-edit:test $Repo
    if ($LASTEXITCODE -ne 0) { throw "Building the test image failed." }
}

Step ("Running the tests" + $(if ($Gpu) { " with the NVIDIA card" } else { " on the processor" }))
$runArgs = @("run", "--rm", "-v", "la-edit-test-models:/data/models")
if ($Gpu) { $runArgs += @("--gpus", "all") }
docker @runArgs latest-avenue-edit:test
$code = $LASTEXITCODE
if ($Gpu -and $code -eq 125) {
    Write-Host "Docker could not give the container the graphics card; running on the processor instead."
    docker run --rm -v la-edit-test-models:/data/models latest-avenue-edit:test
    $code = $LASTEXITCODE
}
Write-Host ""
if ($code -eq 0) { Write-Host "All tests passed." -ForegroundColor Green }
else { Write-Host "Some tests failed (exit code $code): the lines marked FAIL above say which." -ForegroundColor Red }
exit $code
