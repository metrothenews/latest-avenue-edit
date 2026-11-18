# The edit server as a stack (Cloudflare Tunnel, job worker, storage)

This replaces the single `latest-avenue-edit` container and Tailscale Funnel
([README.md](README.md)) with a small Docker Compose stack on the same PC. Your data folder
stays where it is. The website is then served from a fixed address,
`https://edit-api.latestavenue.com`.

```
browser ──https──▶ Cloudflare ──tunnel──▶ cloudflared ─▶ gateway :8080 (sign-in check; the editor inside)
                                                          │  heavy steps as jobs
                                                          ▼
                              redis (job queue, on disk) ◀─ worker ─▶ s3 (SeaweedFS: users/<uid>/…)
                                                    heartbeat ─▶ Firestore editServer/status (every minute)
```

| Service | What it does |
|---|---|
| `gateway` | The only way in. It checks the Google sign-in, runs the editor and answers `/api/rpc`, `/stream` and the public `/status`. Port 8080 is bound to 127.0.0.1 only. |
| `worker` | Runs every heavy step as a queued job: Drive import (with probe and 140p proxies), transcription and the assembly, render, export, Drive upload. The tool names and the contract in [../TOOLS.md](../TOOLS.md) are unchanged. Every night at 03:00 it backs up `projects/` and the editing scripts. |
| `redis` | The job queue, saved to disk (append-only file). After a reboot, unfinished jobs run again. A job that needed a Drive token waits as `needs_token`, because tokens are only ever kept in memory. |
| `s3` | S3-compatible storage (SeaweedFS). After each job, outputs and imported files are copied to `users/<uid>/…`; backups go to `backups/<uid>/`, and the newest 14 are kept. The data folder is the working copy. |
| `heartbeat` | Writes `{lastSeen, version, online, queueDepth, sessionsFree}` to Firestore `editServer/status` every 60 s, so the site can say "offline" when the PC is off. |
| `cloudflared` | The Cloudflare Tunnel. No router ports are opened. |

Every service restarts by itself (`restart: unless-stopped`) and has a health check.

**MinIO was swapped for SeaweedFS.** MinIO's community Docker images are no longer published, and pulls fail. SeaweedFS speaks the same S3 API. The code only talks plain S3 (`scripts/modules/vse_standalone/assembly/s3.py`), so switching later to MinIO, Google Cloud Storage (HMAC keys) or AWS means changing four `S3_*` settings.

**The session runs inside the gateway container for now**, as it did before: one editing session at a time (`sessions_free` is 1 or 0). Splitting sessions into their own containers is the next step, not this one.

## What you do, once

You need Docker Desktop (already installed), the repository checkout `install.ps1` was run from, and access to:

* the Cloudflare account;
* the domain registrar for latestavenue.com;
* the Firebase console for `latest-avenue-app`.

### 1. Move latestavenue.com's DNS to Cloudflare, keeping the website on Firebase

The tunnel's fixed address needs the domain's DNS at Cloudflare. The website itself stays on Firebase Hosting.

1. Go to Cloudflare dashboard > **Add a domain** > `latestavenue.com` > the Free plan. Cloudflare imports the records it finds.
2. **Before changing anything at the registrar,** check that every record from your current DNS provider is in Cloudflare's list. These matter most:
   * **The Firebase Hosting records.** In the Firebase console go to Hosting > your custom domain (`latestavenue.com`, and `www` if listed) > **View** / "DNS records". Copy exactly what it shows. Usually that means:
     * `A` record, name `@`, the IP address(es) Firebase lists (for example `199.36.158.100`);
     * `TXT` record, name `@`, `hosting-site=latest-avenue-app` (or the verification value shown);
     * for `www`: whatever the console shows for it (an `A` record to the same IP, or a `CNAME` to `latest-avenue-app.web.app`).
   * **Any `MX`, `TXT` (SPF, DKIM, `google-site-verification`) or `CNAME` records for email** and other services.
3. Set every Firebase record to **DNS only (grey cloud)**, not proxied. Firebase issues its own certificate and needs to see the requests directly. If a record is proxied, the site can break while the certificate renews.
4. At the domain registrar, change the nameservers to the two that Cloudflare gives you. Wait until Cloudflare says the domain is **Active**. That can take minutes, or up to a day.
5. Check that `https://latestavenue.com` still loads. Firebase Hosting > custom domain should still say **Connected**.

### 2. Create the tunnel

1. In Cloudflare dashboard > **Zero Trust** > Networks > **Tunnels** > Create a tunnel > **Cloudflared**, name it `latest-avenue-edit`.
2. On the "Install connector" page, choose Docker. **Copy only the long token** after `--token`. Don't run the command it shows: compose runs cloudflared for you.
3. Next > **Public hostname**:
   * Subdomain: `edit-api`
   * Domain: `latestavenue.com`
   * Service: **HTTP**, URL `gateway:8080`

   WebSockets work by default (the editor's picture uses them). Leave "Cloudflare Access" off: the gateway does the sign-in check.
4. Save. Cloudflare creates the `edit-api` DNS record (proxied, orange cloud). That one is correct as it is.

### 3. Fill in `.env`

In PowerShell, from the checkout:

```powershell
cd $env:USERPROFILE\latest-avenue-edit
git pull
powershell -ExecutionPolicy Bypass -File cloud\pc\server.ps1 -Init
notepad cloud\pc\.env
```

`-Init` writes `cloud\pc\.env` with:

* the project, owner and origins filled in;
* new random keys for the worker and storage;
* `DATA_DIR` set to your current data folder;
* `USE_GPU=1` if it finds the NVIDIA card.

Paste the tunnel token after `TUNNEL_TOKEN=` and save. **`.env` is git-ignored: never commit it, send it or paste it anywhere.** If the token leaks, delete the tunnel in Cloudflare and make a new one.

### 4. Swap the single container for the stack

First rebuild the image with this version, same as an update (README.md, "Put the new version live", steps 1-3). Then stop the old container and keep it for rollback:

```powershell
docker stop latest-avenue-edit
docker rename latest-avenue-edit latest-avenue-edit-old
tailscale funnel --https=443 off
powershell -ExecutionPolicy Bypass -File cloud\pc\server.ps1
```

The last command prints every service as running or healthy, then the status line, for example `{"online": true, "version": "...", "queue_depth": 0, "sessions_free": 1}`.

Check from any network (a phone off Wi-Fi):

```
https://edit-api.latestavenue.com/status
```

**Roll back:**

```powershell
server.ps1 -Down
docker rename latest-avenue-edit-old latest-avenue-edit
docker start latest-avenue-edit
tailscale funnel --bg --https=443 http://127.0.0.1:8080
```

Once the stack has run well for a week, remove the old container with `docker rm latest-avenue-edit-old`.

### 5. Start at sign-in, never sleep

```powershell
powershell -ExecutionPolicy Bypass -File cloud\pc\server.ps1 -Install
```

This does three things:

* Registers the task **"latest avenue edit server"**. At sign-in it waits for Docker Desktop and then starts the stack. Its log is in `%LOCALAPPDATA%\latest-avenue-server.log`.
* Turns on Docker Desktop's **Start when you sign in**.
* Sets the PC never to sleep or hibernate on mains power (`powercfg /change standby-timeout-ac 0` and `hibernate-timeout-ac 0`).

Two more settings by hand, for a server that comes back after a power cut:

* **Sign in automatically.** Run `netplwiz` and untick "Users must enter a user name and password". On Windows 11 you may first need to turn off "For improved security, only allow Windows Hello sign-in" in Settings > Accounts > Sign-in options. Docker Desktop only runs once someone is signed in.
* **Power on after a power cut.** In the BIOS/UEFI, set "Restore on AC power loss" to **Power On**, if the board has it.

### 6. The heartbeat (offline notice on the site)

1. Go to Google Cloud console > project `latest-avenue-app` > IAM & Admin > **Service accounts** > Create:
   * name `edit-heartbeat`;
   * role **Cloud Datastore User** (`roles/datastore.user`). Give it nothing else.
2. Open the account > Keys > Add key > JSON. Save the file as `cloud\pc\secrets\heartbeat-sa.json`. The folder is git-ignored.
3. In Firestore rules, let anyone read the status document and nobody write it from a browser (the service account isn't bound by the rules):

   ```
   match /editServer/{doc} {
     allow read: if doc == "status";
     allow write: if false;
   }
   ```
4. Run `server.ps1` again. `docker compose logs heartbeat` should then print `beat` once a minute.

### 7. The site

The site's code lives in the `latest-avenue` repo; that change is separate work:

* Set `editServiceUrl` (`src/data/edit.js`) to `https://edit-api.latestavenue.com`.
* Before connecting, read `GET https://edit-api.latestavenue.com/status`. It needs no sign-in, works from any origin and gives `{online, version, queue_depth, sessions_free}`.
* If that request fails, read Firestore `editServer/status`. When `lastSeen` is more than about 3 minutes old, show "the edit server is offline".

## Day to day

```powershell
server.ps1 -Status         # services and the status line
server.ps1                 # after an update (rebuild the image first): restarts what changed
server.ps1 -Down           # stop; nothing is lost
docker compose --project-directory cloud\pc --env-file cloud\pc\.env logs -f worker
```

* Jobs survive restarts: whatever was running runs again, and imports come back paused, so resume them from the site.
* Backups: `backups/<uid>/projects-<date>.tar.gz` in the storage service, one a night, the newest 14 kept. To take one now:

  ```powershell
  docker compose --project-directory cloud\pc exec worker python3 -c "import urllib.request,os; print(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8795/backup', method='POST', data=b'{}', headers={'X-Worker-Key': os.environ['WORKER_KEY']})).read())"
  ```
* Storage space: the SeaweedFS volume `latest-avenue_s3-data` holds a second copy of the media. Docker Desktop's disk image is where it lives (Settings > Resources > Disk image location).

## What has and hasn't been run

These were run in a Linux container, with stand-ins for Google. `cloud/test_stack.sh` repeats all of them:

* The whole stack: `docker compose` with every service healthy.
* The public `/status` (CORS `*`).
* The website's full `/edit` flow over `/api/rpc`, through the gateway, with assembly, render and exports run as worker jobs and their outputs in storage.
* The Drive import queue through gateway > worker > storage, against a stand-in Drive.
* A worker killed mid-job: the job ran again after restart, and a token job came back `needs_token`.
* A backup.
* The heartbeat, against a stand-in Firestore and token server that checks the service-account signature.

These have not been run: Windows (`server.ps1`), the real Cloudflare tunnel, real Firestore, and the GPU override (`compose.gpu.yaml`).
