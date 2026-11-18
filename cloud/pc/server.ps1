# SPDX-FileCopyrightText: 2026 VSE Standalone contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

<#
The edit server as a Docker Compose stack (compose.yaml): gateway + editor, job worker, job
queue (Redis), storage (SeaweedFS), Firestore heartbeat and the Cloudflare Tunnel. See SERVER.md.

  powershell -ExecutionPolicy Bypass -File cloud\pc\server.ps1 -Init      # make .env (secrets generated)
  powershell -ExecutionPolicy Bypass -File cloud\pc\server.ps1            # start / update it
  powershell -ExecutionPolicy Bypass -File cloud\pc\server.ps1 -Install   # start at sign-in, no sleep
  powershell -ExecutionPolicy Bypass -File cloud\pc\server.ps1 -Status
  powershell -ExecutionPolicy Bypass -File cloud\pc\server.ps1 -Down      # stop it (data kept)

-Gpu gives the worker the NVIDIA card (the image must be built with ASSEMBLY_CUDA=1, which
install.ps1 does when it finds one). -NoTunnel starts everything but cloudflared.
-Wait is how the sign-in task calls it: wait up to 10 minutes for Docker Desktop first.
#>

param(
    [switch]$Init,
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status,
    [switch]$Down,
    [switch]$Gpu,
    [switch]$NoTunnel,
    [switch]$Wait,
    [string]$DataDir = "$env:USERPROFILE\latest-avenue-edit-data"
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$EnvFile = Join-Path $Here ".env"
$TaskName = "latest avenue edit server"
$Log = Join-Path $env:LOCALAPPDATA "latest-avenue-server.log"

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
function Compose() {
    $files = @("-f", (Join-Path $Here "compose.yaml"))
    $useGpu = $Gpu -or ((ReadEnv)["USE_GPU"] -eq "1")
    if ($useGpu) { $files += @("-f", (Join-Path $Here "compose.gpu.yaml")) }
    $prof = if ($NoTunnel) { @() } else { @("--profile", "tunnel") }
    return @("compose", "--project-directory", $Here, "--env-file", $EnvFile) + $files + $prof
}

# ---------------------------------------------------------------------------
if ($Init) {
    Step "Making $EnvFile"
    if (Test-Path $EnvFile) { Fail ".env already exists; edit it instead (or delete it to start over)." }
    $text = Get-Content (Join-Path $Here "env.example") -Raw
    $set = @{
        "FIREBASE_PROJECT_ID" = "latest-avenue-app";
        "ALLOWED_UIDS" = "cUUEuJ7NRCZVuMNhSxOr7yzA8DX2";
        "ALLOWED_ORIGINS" = "https://latestavenue.com,https://www.latestavenue.com,https://latest-avenue-app.web.app";
        "WORKER_KEY" = (Secret); "S3_ACCESS_KEY" = (Secret).Substring(0, 20); "S3_SECRET_KEY" = (Secret);
        "DATA_DIR" = ($DataDir -replace '\\', '/')
    }
    foreach ($k in $set.Keys) { $text = $text -replace "(?m)^$k=.*$", "$k=$($set[$k])" }
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $text += "`r`nUSE_GPU=1`r`n" }
    [IO.File]::WriteAllText($EnvFile, $text)
    New-Item -ItemType Directory -Force -Path (Join-Path $Here "secrets") | Out-Null
    Write-Host "Done. Open $EnvFile and paste the Cloudflare tunnel token after TUNNEL_TOKEN= (SERVER.md, step 2)."
    Write-Host "The random keys in it are already filled in. Never commit or share this file."
    exit 0
}

if (-not (Test-Path $EnvFile)) { Fail "no .env yet: run this with -Init first (SERVER.md)." }

if ($Install) {
    Step "Starting the server at sign-in"
    $script = $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Wait"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Starts the latest avenue edit server (docker compose) once Docker Desktop is up." -Force | Out-Null
    Write-Host "Registered '$TaskName' (at sign-in, as $env:USERNAME). Log: $Log"

    # Docker Desktop itself starts at sign-in (Settings > General > Start Docker Desktop when you sign in).
    foreach ($f in @("$env:APPDATA\Docker\settings-store.json", "$env:APPDATA\Docker\settings.json")) {
        if (Test-Path $f) {
            $json = Get-Content $f -Raw | ConvertFrom-Json
            $key = if ($f -like "*settings-store.json") { "AutoStart" } else { "autoStart" }
            $json | Add-Member -NotePropertyName $key -NotePropertyValue $true -Force
            [IO.File]::WriteAllText($f, ($json | ConvertTo-Json -Depth 20))
            Write-Host "Docker Desktop: start at sign-in turned on ($f)."
            break
        }
    }

    Step "Power: never sleep when plugged in"
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    Write-Host "Done: the PC no longer sleeps or hibernates on mains power (the screen can still turn off)."
    Write-Host "Undo with: powercfg /change standby-timeout-ac 30"
    exit 0
}

if ($Uninstall) {
    Quiet { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
    Write-Host "The sign-in task is removed. The stack keeps running until: server.ps1 -Down"
    exit 0
}

# ---------------------------------------------------------------------------
if ($Wait) {
    Start-Transcript -Path $Log -Append | Out-Null
    $ready = $false
    foreach ($try in 1..120) {
        Quiet { docker info }
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) { Fail "Docker Desktop did not start within 10 minutes." }
}

Quiet { docker info }
if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop is not running. Start it, wait until it says it is running, then run this again." }
$dc = Compose

if ($Down) {
    docker @dc down
    Write-Host "Stopped. Data, queue and storage are kept (volumes and $((ReadEnv)['DATA_DIR']))."
    exit 0
}

if ($Status) {
    docker @dc ps
    try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:8080/status").Content } catch { Write-Host "gateway: not answering" }
    exit 0
}

Step "Starting the stack"
# The single-container editor (install.ps1) uses the same port: it has to go first (SERVER.md, step 4).
$old = (docker ps -a --filter "name=^/latest-avenue-edit$" --format "{{.Names}}")
if ($old) { Fail "the old single container 'latest-avenue-edit' is still there. Follow SERVER.md step 4 (it keeps it for rollback)." }
$values = ReadEnv
if (-not $values["TUNNEL_TOKEN"] -and -not $NoTunnel) { Fail "TUNNEL_TOKEN is empty in .env (SERVER.md, step 2), or run with -NoTunnel." }
if ($values["DATA_DIR"]) { New-Item -ItemType Directory -Force -Path $values["DATA_DIR"] | Out-Null }
docker @dc up -d --remove-orphans
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed (exit code $LASTEXITCODE). The messages above say why." }

$ok = $false
foreach ($try in 1..60) {
    try {
        $reply = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:8080/status"
        if ($reply.StatusCode -eq 200) { $ok = $true; break }
    } catch { Start-Sleep -Seconds 2 }
}
docker @dc ps
if (-not $ok) { Fail "the gateway did not answer on 127.0.0.1:8080. See: docker compose logs gateway worker" }
Write-Host ""
Write-Host "Running: $($reply.Content)"
Write-Host "Public: https://edit-api.latestavenue.com/status (once the tunnel and DNS are set up, SERVER.md)."
