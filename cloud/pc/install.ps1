# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

<#
.SYNOPSIS
Sets up (or updates) this Windows PC as the server for latest avenue video.

.DESCRIPTION
The editor runs on this PC with no window, inside a container (Docker Desktop), and is shown
at https://latestavenue.com/edit. All the work (decoding, effects, rendering) happens on
this PC. Only the site's owner account can start or use it: the gateway in the container
checks the Google sign-in on every request.

Run it again to update: it pulls the latest code, rebuilds the image and restarts the server.
The project and footage live in the data folder on this PC and are kept.

.PARAMETER DataDir
Folder on this PC for projects, media and renders. Appears in the editor as /data.

.PARAMETER Port
Local port for the gateway. It listens on this PC only (127.0.0.1); a tunnel gives it its
public address.

.PARAMETER Tunnel
How the site reaches this PC: "tailscale" (Tailscale Funnel: free, a fixed https address,
no router changes) or "none" (you provide the public https address yourself).

.PARAMETER OwnerUids
Firebase user IDs allowed to use the editor, comma-separated. Default: the site's owner.

.PARAMETER AppTarball
Path to latest-avenue-video-linux-x64.tar.xz, to use a specific build file. Without it the
file is fetched from the repository's "linux-build" branch.

.PARAMETER Cpus
How many processor cores the editor may use (default: all but two, at least two).

.PARAMETER Memory
Memory limit for the editor, e.g. "8g" (default: no limit beyond Docker's own).

.PARAMETER NoPull
Do not pull new code first.

.PARAMETER Uninstall
Stop and remove the server (the data folder is kept).

.EXAMPLE
powershell -ExecutionPolicy Bypass -File cloud\pc\install.ps1

.EXAMPLE
powershell -ExecutionPolicy Bypass -File cloud\pc\install.ps1 -DataDir D:\edit -Tunnel none
#>

param(
    [string]$DataDir = "$env:USERPROFILE\latest-avenue-edit-data",
    [int]$Port = 8080,
    [ValidateSet("tailscale", "none")][string]$Tunnel = "tailscale",
    [string]$OwnerUids = "cUUEuJ7NRCZVuMNhSxOr7yzA8DX2",
    [string]$ProjectId = "latest-avenue-app",
    [string]$Origins = "https://latestavenue.com,https://www.latestavenue.com,https://latest-avenue-app.web.app",
    [string]$AppTarball = "",
    [int]$Cpus = 0,
    [string]$Memory = "",
    [string]$Geometry = "1600x900",
    [switch]$NoPull,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$Name = "latest-avenue-edit"
$Image = "latest-avenue-edit:local"
$ReleaseFile = "latest-avenue-video-linux-x64.tar.xz"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor White }
function Fail($text) { Write-Host ""; Write-Host "Stopped: $text" -ForegroundColor Red; exit 1 }
function Have($command) { return [bool](Get-Command $command -ErrorAction SilentlyContinue) }

# Runs a program whose output is not wanted. Windows PowerShell 5.1 turns anything a program
# writes to its error stream into a failure when that stream is redirected, so the error
# setting is relaxed just for the call; check $LASTEXITCODE afterwards.
function Quiet([scriptblock]$block) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $block *> $null } catch { }
    $ErrorActionPreference = $previous
}

# Runs a program and stops with a message if it fails.
function Run($what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE)." }
}

# ---------------------------------------------------------------------------
Step "Checking this PC"

if (-not [Environment]::Is64BitOperatingSystem) { Fail "a 64-bit version of Windows is needed." }
if (-not (Have "docker")) {
    Fail ("Docker Desktop is needed. Install it from https://www.docker.com/products/docker-desktop/ " +
          "(or run: winget install Docker.DockerDesktop), start it once, then run this again.")
}
Quiet { docker info }
if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop is installed but not running. Start it, wait until it says it is running, then run this again." }
$osType = (docker info --format "{{.OSType}}")
if ($osType -ne "linux") { Fail "Docker Desktop is set to Windows containers. Right-click its tray icon and choose 'Switch to Linux containers'." }
Write-Host "Docker: $(docker version --format '{{.Server.Version}}')"

if ($Uninstall) {
    Step "Removing the server"
    Quiet { docker rm -f $Name }
    if (Have "tailscale") { Quiet { tailscale funnel --https=443 off } }
    Write-Host "Removed. Your files are still in $DataDir."
    exit 0
}

# ---------------------------------------------------------------------------
if (-not $NoPull -and (Have "git") -and (Test-Path (Join-Path $Repo ".git"))) {
    Step "Getting the latest code"
    $changes = (git -C $Repo status --porcelain)
    if ($changes) {
        Write-Host "You have local changes, keeping them and skipping the update of the code."
    } else {
        $env:GIT_LFS_SKIP_SMUDGE = "1"
        git -C $Repo pull --ff-only
        if ($LASTEXITCODE -ne 0) { Write-Host "Could not pull (offline?), using the code that is here." }
    }
}

# ---------------------------------------------------------------------------
Step "Finding the editor's Linux build"

$target = Join-Path $Repo $ReleaseFile

# The build file is kept on the repository's "linux-build" branch, split in two parts
# (GitHub refuses files over 100 MB). Fetch that branch into a temporary folder and join them.
function Get-BuildFromBranch($destination) {
    if (-not (Have "git")) { return $false }
    $url = (git -C $Repo remote get-url origin)
    if (-not $url) { return $false }
    $temp = Join-Path ([IO.Path]::GetTempPath()) ("la-linux-build-" + [guid]::NewGuid().ToString("N"))
    $env:GIT_LFS_SKIP_SMUDGE = "1"
    git clone --depth 1 --single-branch --branch linux-build $url $temp
    if ($LASTEXITCODE -ne 0) { return $false }
    $parts = Get-ChildItem -Path $temp -Filter "$ReleaseFile.part*" | Sort-Object Name
    if (-not $parts) { Remove-Item -Recurse -Force $temp; return $false }
    $out = [IO.File]::Create($destination)
    try {
        foreach ($part in $parts) {
            $in = [IO.File]::OpenRead($part.FullName)
            try { $in.CopyTo($out) } finally { $in.Dispose() }
        }
    } finally { $out.Dispose() }
    $sums = Join-Path $temp "SHA256SUMS"
    $ok = $true
    if (Test-Path $sums) {
        $expected = ((Get-Content $sums | Select-Object -First 1) -split "\s+")[0].ToLower()
        $actual = (Get-FileHash -Algorithm SHA256 $destination).Hash.ToLower()
        if ($expected -ne $actual) {
            Write-Host "The joined file does not match its checksum (expected $expected, got $actual)."
            Remove-Item -Force $destination
            $ok = $false
        }
    }
    Remove-Item -Recurse -Force $temp
    return $ok
}

if ($AppTarball) {
    if (-not (Test-Path $AppTarball)) { Fail "$AppTarball does not exist." }
    Copy-Item $AppTarball $target -Force
} elseif (-not (Test-Path $target)) {
    $downloads = Join-Path $env:USERPROFILE "Downloads\$ReleaseFile"
    if (Test-Path $downloads) {
        Copy-Item $downloads $target
    } elseif (Get-BuildFromBranch $target) {
        Write-Host "Fetched the build from the linux-build branch."
    } elseif (Have "gh") {
        gh release download --repo metrothenews/latest-avenue-edit --pattern $ReleaseFile --dir $Repo
        if ($LASTEXITCODE -ne 0) { Fail "could not get $ReleaseFile from the linux-build branch or the GitHub release." }
    } else {
        Fail "$ReleaseFile could not be fetched from the linux-build branch. Put the file next to the repository's README and run this again."
    }
}
$sizeMb = [math]::Round((Get-Item $target).Length / 1MB)
if ($sizeMb -lt 50) { Fail "$target is only $sizeMb MB, which is not a complete download." }
Write-Host "Using $target ($sizeMb MB)"

# ---------------------------------------------------------------------------
Step "Building the image (first time: a few minutes)"

$env:DOCKER_BUILDKIT = "1"
Run "Building the image" { docker build -f (Join-Path $Repo "cloud\Dockerfile") -t $Image $Repo }

# ---------------------------------------------------------------------------
Step "Starting the server"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
foreach ($sub in @("projects", "media", "renders")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DataDir $sub) | Out-Null
}
if ($Cpus -le 0) { $Cpus = [math]::Max(2, [Environment]::ProcessorCount - 2) }

Quiet { docker rm -f $Name }
$runArgs = @(
    "run", "-d", "--name", $Name,
    "--restart", "unless-stopped",
    # 127.0.0.1 only: nothing on the network or the internet reaches this port directly.
    "-p", "127.0.0.1:${Port}:8080",
    "-v", "${DataDir}:/data",
    "-e", "FIREBASE_PROJECT_ID=$ProjectId",
    "-e", "ALLOWED_UIDS=$OwnerUids",
    "-e", "ALLOWED_ORIGINS=$Origins",
    "-e", "VSE_GEOMETRY=$Geometry",
    "--cpus", "$Cpus"
)
if ($Memory) { $runArgs += @("--memory", $Memory) }
$runArgs += $Image
Run "Starting the server" { docker @runArgs | Out-Null }

$healthy = $false
foreach ($try in 1..30) {
    try {
        $reply = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:$Port/healthz"
        if ($reply.StatusCode -eq 200) { $healthy = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $healthy) {
    docker logs --tail 30 $Name
    Fail "the server did not answer on port $Port. Its last messages are above."
}
Write-Host "The gateway is running on this PC (port $Port, $Cpus cores)."

# ---------------------------------------------------------------------------
$address = ""
if ($Tunnel -eq "tailscale") {
    Step "Public address (Tailscale Funnel)"
    if (-not (Have "tailscale")) {
        Write-Host "Tailscale is not installed. Install it from https://tailscale.com/download (or run:"
        Write-Host "winget install tailscale.tailscale), sign in, then run this again."
        Write-Host "The server itself is running; it just has no public address yet."
    } else {
        tailscale funnel --bg --https=443 "http://127.0.0.1:$Port"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Tailscale could not turn Funnel on. If it printed a link above, open it to allow"
            Write-Host "Funnel for your account, then run this again."
        } else {
            $status = (tailscale funnel status) -join "`n"
            $match = [regex]::Match($status, "https://[A-Za-z0-9\.\-]+")
            if ($match.Success) { $address = $match.Value.TrimEnd("/") }
        }
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor White
if ($address) {
    Write-Host "This PC's address for the site:"
    Write-Host ""
    Write-Host "  $address"
    Write-Host ""
    Write-Host "Put that in editServiceUrl in src/data/edit.js of the latest-avenue repo, then build"
    Write-Host "and deploy the site. The editor is then at https://latestavenue.com/edit (owner sign-in)."
} else {
    Write-Host "Give the gateway (http://127.0.0.1:$Port on this PC) a public https address, then put"
    Write-Host "that address in editServiceUrl in src/data/edit.js of the latest-avenue repo."
}
Write-Host "Footage: put it in $DataDir\media (it appears in the editor at /data/media)."
Write-Host "Renders arrive in $DataDir\renders. To update later, run this again."
Write-Host "The PC has to be on and awake, with Docker Desktop running, for the page to connect."
