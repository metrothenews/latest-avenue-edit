# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

<#
The one-time setup of the edit server stack (SERVER.md), and a few local checks. After the
setup nothing has to be run on the PC: releases arrive through the updater service.

  powershell -ExecutionPolicy Bypass -File server.ps1 -Init [-HeartbeatKey key.json]   # make .env
  powershell -ExecutionPolicy Bypass -File server.ps1                                  # start it
  powershell -ExecutionPolicy Bypass -File server.ps1 -ImportData C:\Users\you\latest-avenue-edit-data
  powershell -ExecutionPolicy Bypass -File server.ps1 -Install    # never sleep; Docker at sign-in
  powershell -ExecutionPolicy Bypass -File server.ps1 -Status
  powershell -ExecutionPolicy Bypass -File server.ps1 -Down       # stop it (data kept)

Run it from the folder that holds .env (the checkout's cloud\pc, or any folder you copy
server.ps1 and env.example into). Starting takes the stack's files (compose.yaml, ...) out
of the released image into this folder, so they are always the released ones.
#>

param(
    [switch]$Init,
    [string]$HeartbeatKey = "",
    [string]$ImportData = "",
    [switch]$Install,
    [switch]$Status,
    [switch]$Down
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$EnvFile = Join-Path $Here ".env"
$Project = "latest-avenue"

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor White }
function Fail($text) { Write-Host ""; Write-Host "Stopped: $text" -ForegroundColor Red; exit 1 }
function Quiet([scriptblock]$block) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $block *> $null } catch { }
    $ErrorActionPreference = $previous
}
function Secret() { return ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")) }
function ReadEnv() {
    $values = @{}
    foreach ($line in (Get-Content $EnvFile)) {
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
    }
    return $values
}
function SetEnv($text, $key, $value) {
    if ($text -match "(?m)^$key=") { return ($text -replace "(?m)^$key=.*$", "$key=$value") }
    return ($text.TrimEnd() + "`r`n$key=$value`r`n")
}
function Compose() {
    $files = @("-f", (Join-Path $Here "compose.yaml"))
    if ((ReadEnv)["USE_GPU"] -eq "1") { $files += @("-f", (Join-Path $Here "compose.gpu.yaml")) }
    return @("compose", "-p", $Project, "--project-directory", $Here, "--env-file", $EnvFile) + $files
}
function NeedDocker() {
    Quiet { docker info }
    if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop is not running. Start it, wait until it says it is running, then run this again." }
}

# ---------------------------------------------------------------------------
if ($Init) {
    Step "Making $EnvFile"
    $text = if (Test-Path $EnvFile) { Get-Content $EnvFile -Raw } else { Get-Content (Join-Path $Here "env.example") -Raw }
    $values = if (Test-Path $EnvFile) { ReadEnv } else { @{} }
    $defaults = [ordered]@{
        "FIREBASE_PROJECT_ID" = "latest-avenue-app";
        "ALLOWED_UIDS" = "cUUEuJ7NRCZVuMNhSxOr7yzA8DX2";
        "ALLOWED_ORIGINS" = "https://latestavenue.com,https://www.latestavenue.com,https://latest-avenue-app.web.app";
        "WORKER_KEY" = (Secret); "S3_ACCESS_KEY" = (Secret).Substring(0, 20); "S3_SECRET_KEY" = (Secret)
    }
    foreach ($k in $defaults.Keys) { if (-not $values[$k]) { $text = SetEnv $text $k $defaults[$k] } }
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $text = SetEnv $text "USE_GPU" "1" }
    if ($HeartbeatKey) {
        if (-not (Test-Path $HeartbeatKey)) { Fail "$HeartbeatKey does not exist." }
        $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Resolve-Path $HeartbeatKey)))
        $text = SetEnv $text "HEARTBEAT_SA_B64" $b64
        Write-Host "The heartbeat key is in .env now: delete $HeartbeatKey."
    }
    [IO.File]::WriteAllText($EnvFile, $text)
    $missing = @("TUNNEL_TOKEN", "GHCR_USER", "GHCR_TOKEN") | Where-Object { -not (ReadEnv)[$_] }
    if ($missing) { Write-Host "Still to fill in (open it with: notepad `"$EnvFile`"): $($missing -join ', ')" }
    Write-Host "The random keys are filled in. Never commit, send or paste this file anywhere."
    exit 0
}

if (-not (Test-Path $EnvFile)) { Fail "no .env here yet: run this with -Init first (SERVER.md)." }

if ($Install) {
    Step "Docker Desktop at sign-in"
    foreach ($f in @("$env:APPDATA\Docker\settings-store.json", "$env:APPDATA\Docker\settings.json")) {
        if (Test-Path $f) {
            $json = Get-Content $f -Raw | ConvertFrom-Json
            $key = if ($f -like "*settings-store.json") { "AutoStart" } else { "autoStart" }
            $json | Add-Member -NotePropertyName $key -NotePropertyValue $true -Force
            [IO.File]::WriteAllText($f, ($json | ConvertTo-Json -Depth 20))
            Write-Host "Turned on ($f). The stack itself starts with Docker (restart: unless-stopped)."
            break
        }
    }
    Step "Power: never sleep when plugged in"
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    Write-Host "The PC no longer sleeps or hibernates on mains power (the screen can still turn off)."
    Write-Host "Undo with: powercfg /change standby-timeout-ac 30"
    exit 0
}

NeedDocker
$values = ReadEnv
$image = if ($values["EDIT_IMAGE"]) { $values["EDIT_IMAGE"] } else { "ghcr.io/metrothenews/latest-avenue-edit:stable" }

if ($Down) { $dc = Compose; docker @dc down; Write-Host "Stopped. Data, queue and storage are kept (Docker volumes)."; exit 0 }
if ($Status) {
    $dc = Compose; docker @dc ps
    try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:8080/status").Content } catch { Write-Host "gateway: not answering" }
    exit 0
}

if ($ImportData) {
    Step "Copying $ImportData into the stack's data volume"
    if (-not (Test-Path $ImportData)) { Fail "$ImportData does not exist." }
    $dc = Compose
    docker @dc stop gateway worker
    docker run --rm -u root -v "${ImportData}:/src:ro" -v "${Project}_edit-data:/dst" $image `
        sh -c "cp -a /src/. /dst/ && chown -R 1001:1001 /dst"
    if ($LASTEXITCODE -ne 0) { Fail "the copy failed (exit code $LASTEXITCODE)." }
    docker @dc start worker gateway
    Write-Host "Copied. The folder itself is left as it was; delete it once everything looks right."
    exit 0
}

# ---------------------------------------------------------------------------
Step "Signing in to the image registry"
foreach ($k in @("GHCR_USER", "GHCR_TOKEN", "TUNNEL_TOKEN")) {
    if (-not $values[$k]) { Fail "$k is empty in .env (SERVER.md, steps 2-3)." }
}
$values["GHCR_TOKEN"] | docker login ghcr.io -u $values["GHCR_USER"] --password-stdin
if ($LASTEXITCODE -ne 0) { Fail "the registry refused the token. Check GHCR_USER and GHCR_TOKEN (read:packages)." }

Step "Getting the released image"
docker pull $image
if ($LASTEXITCODE -ne 0) { Fail "could not pull $image." }
$cid = (docker create $image true)
foreach ($f in @("compose.yaml", "compose.gpu.yaml", "env.example")) {
    docker cp "${cid}:/opt/cloud/pc/$f" (Join-Path $Here $f) | Out-Null
}
docker rm -f $cid | Out-Null
Write-Host "Version: $(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' $image)"

Step "Starting the stack"
$old = (docker ps -a --filter "name=^/latest-avenue-edit$" --format "{{.Names}}")
if ($old) { Fail "the old single container 'latest-avenue-edit' is still there. Follow SERVER.md step 5 (it keeps it for rollback)." }
$dc = Compose
docker @dc up -d
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed (exit code $LASTEXITCODE). The messages above say why." }

$ok = $false
foreach ($try in 1..90) {
    try {
        $reply = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:8080/status"
        if ($reply.StatusCode -eq 200) { $ok = $true; break }
    } catch { Start-Sleep -Seconds 2 }
}
docker @dc ps
if (-not $ok) { Fail "the gateway did not answer on 127.0.0.1:8080. See: docker compose -p $Project logs gateway worker" }
Write-Host ""
Write-Host "Running: $($reply.Content)"
Write-Host "Public: https://edit-api.latestavenue.com/status. From now on, releases install themselves."
