# The edit server: one-time setup, then releases from GitHub

This replaces the single `latest-avenue-edit` container and Tailscale Funnel
([README.md](README.md)) with a Docker Compose stack on the same PC, at a fixed address,
`https://edit-api.latestavenue.com`. You set the PC up once. After that, you update, merge
and release from the Mac or GitHub, and nobody touches the PC. It also needs no Claude
sessions.

```
browser ──https──▶ Cloudflare ──tunnel──▶ cloudflared ─▶ gateway :8080 (sign-in check; the editor inside)
                                                          │  heavy steps as jobs
                                                          ▼
                              redis (job queue, on disk) ◀─ worker ─▶ s3 (SeaweedFS: users/<uid>/…)
                                                    heartbeat ─▶ Firestore editServer/status (every minute)
   GitHub: push to main ─▶ tests ─▶ ghcr.io/…:stable  ◀── updater (pulls every 5 min, rolls out, rolls back)
```

| Service | What it does |
|---|---|
| `gateway` | The only way in. It checks the Google sign-in, runs the editor and answers `/api/rpc`, `/stream`, the public `/status` and the owner-only `/api/admin`. Port 8080 is bound to 127.0.0.1 only. |
| `worker` | Runs every heavy step as a queued job: Drive import (with probe and 140p proxies), transcription and assembly, render, export, Drive upload. The contract in [../TOOLS.md](../TOOLS.md) is unchanged. Every night at 03:00 it backs up the projects. |
| `redis` | The job queue, saved to disk. After a restart, unfinished jobs run again. A job that needed a Drive token waits as `needs_token`, because tokens are only ever kept in memory. |
| `s3` | S3-compatible storage (SeaweedFS). It holds outputs and imported files under `users/<uid>/…`, and backups under `backups/<uid>/` (the newest 14 are kept). |
| `heartbeat` | Writes `{lastSeen, version, online, health, update, queueDepth, sessionsFree}` to Firestore `editServer/status` every minute. |
| `cloudflared` | The Cloudflare Tunnel. No router ports are opened. |
| `updater` | Pulls the released image and rolls the stack onto it. If the new version fails its checks, it rolls back by itself. See "How releases work". |

Every service restarts by itself and has a health check. The stack mounts no folders of the
PC: data is in Docker volumes, and every secret is in one file, `.env`.

Two choices made along the way:

* **SeaweedFS instead of MinIO.** MinIO's community images are no longer published. The code speaks plain S3, so moving to MinIO, Google Cloud Storage or AWS later means changing the `S3_*` settings.
* **One editing session at a time.** The session still runs inside the gateway container.

## How releases work (after the setup)

Merge to `main`, from the Mac or on github.com. That's the whole release. What follows
happens by itself:

1. **GitHub Actions** (workflow "Edit server", [../../.github/workflows/edit-server.yml](../../.github/workflows/edit-server.yml)) builds the image and runs every test: the container suite, then `cloud/test_stack.sh` (the whole stack, including updates and rollbacks). It takes about 40 minutes.
2. If the tests pass, the image is pushed to GitHub's registry as `ghcr.io/metrothenews/latest-avenue-edit:sha-<commit>`, and the tag **`:stable`** moves to it. The registry is private.
3. **On the PC**, within 5 minutes, the updater pulls `:stable`. If someone is editing, it waits until they stop, for up to 2 hours. Then it:
   * takes the new `compose.yaml` out of the image, so changes to the stack ship the same way;
   * drains the worker and waits for its jobs to finish;
   * restarts the worker, then the gateway, then the heartbeat;
   * checks that every service is healthy and that `/status` reports the new version.
4. **If the checks fail within 4 minutes,** it puts the previous version back and won't try that image again until `:stable` moves.

The PC only ever pulls. It never accepts deploy commands and never runs CI.

* **See what's running.** Open `https://edit-api.latestavenue.com/status`:
  * `version` is the first 12 characters of the commit;
  * `health` is `ok` or `degraded`;
  * `update` is `deployed`, `updating`, `rolled_back` or `failed`.

  Firestore `editServer/status` has the same, even when the PC is off.
* **Roll back.** On GitHub, go to Actions > **Promote** > Run workflow and enter an older tag (`sha-…`, listed on the repository's Packages page). Or ask the PC directly with the admin call `rollback`, below.
* **Hold releases.** In the repository, go to Settings > Secrets and variables > Actions > Variables and add `AUTO_PROMOTE` = `false`. Images are still built and tested, but `:stable` only moves when you run **Promote**.
* **Stop the PC updating.** Add `UPDATE_DISABLED=1` to its `.env`. That is a change on the PC, so use it only for emergencies.

### The owner's admin calls

The calls go through the gateway with your sign-in, the same token the site uses. They are
for an admin page on the site; any HTTP client works too.

```
GET  https://edit-api.latestavenue.com/api/admin/overview          services, versions, health, update state, job queue
GET  https://edit-api.latestavenue.com/api/admin/logs?service=worker&lines=200
POST https://edit-api.latestavenue.com/api/admin/restart   {"service": "worker"}   (drains it first)
POST https://edit-api.latestavenue.com/api/admin/drain | undrain | update | rollback | backup
Authorization: Bearer <Firebase ID token>
```

To try one from the Mac, copy a token from the site: the ID token of the signed-in user
(for example from the Authorization header of any `/api/rpc` request in the browser's
developer tools, Network tab). It lasts an hour. Then:

```
curl -H "Authorization: Bearer <token>" https://edit-api.latestavenue.com/api/admin/overview
```

`logs` works for `gateway`, `worker`, `heartbeat`, `updater`, `cloudflared`, `redis` and `s3`.

## The one-time setup

You need:

* Docker Desktop on the PC;
* the Cloudflare account;
* the domain registrar for latestavenue.com;
* the Firebase / Google Cloud console for `latest-avenue-app`;
* GitHub.

The first CI release must exist before step 6. That means this branch is merged into
`main` and the "Edit server" workflow has run green once.

### 1. Move latestavenue.com's DNS to Cloudflare, keeping the website on Firebase

1. Go to Cloudflare dashboard > **Add a domain** > `latestavenue.com` > the Free plan. Cloudflare imports the records it finds.
2. **Before changing anything at the registrar,** check that every record from your current DNS provider is in Cloudflare's list. These matter most:
   * **The Firebase Hosting records.** In the Firebase console go to Hosting > the custom domain (`latestavenue.com`, and `www` if listed) > **View** / "DNS records". Copy exactly what it shows. Usually that means:
     * `A` record, name `@`, the IP address(es) Firebase lists (for example `199.36.158.100`);
     * `TXT` record, name `@`, `hosting-site=latest-avenue-app` (or the verification value shown);
     * for `www`: what the console shows (an `A` record to the same IP, or a `CNAME` to `latest-avenue-app.web.app`).
   * **Email and other services:** `MX`, `TXT` (SPF, DKIM, `google-site-verification`) and `CNAME` records.
3. Set every Firebase record to **DNS only (grey cloud)**. Firebase issues its own certificate and needs to see the requests directly.
4. At the registrar, change the nameservers to the two Cloudflare gives you. Wait until Cloudflare says **Active**, which can take minutes or up to a day.
5. Check that `https://latestavenue.com` still loads, and that Firebase Hosting > custom domain still says **Connected**.

### 2. Create the tunnel

1. In Cloudflare dashboard > **Zero Trust** > Networks > **Tunnels** > Create a tunnel > **Cloudflared**, name it `latest-avenue-edit`.
2. On "Install connector", **copy only the long token** after `--token`. Don't run the command it shows.
3. Add a **public hostname**:
   * subdomain `edit-api`
   * domain `latestavenue.com`
   * service **HTTP**, URL `gateway:8080`

   WebSockets work by default. Leave Cloudflare Access off: the gateway checks the sign-in.
4. Save. The `edit-api` record Cloudflare creates (proxied, orange cloud) is correct as it is.

### 3. A read-only token for the image registry

1. Check the image is private. After the first CI run, go to github.com > metrothenews > **Packages** > `latest-avenue-edit` > Package settings > Danger zone > visibility **Private**. Check that "Manage Actions access" lists this repository with **Write**.
2. Make the token. Go to github.com > your avatar > Settings > Developer settings > Personal access tokens > **Tokens (classic)** > Generate new token (classic):
   * scope **`read:packages` only**;
   * expiration 1 year (set a reminder). An expired token doesn't break the PC: it keeps running its version, and `/api/admin/overview` shows "pull failed".

   It goes in `.env` as `GHCR_TOKEN`, with your GitHub user name as `GHCR_USER`.

### 4. The heartbeat's service account

1. Go to Google Cloud console > project `latest-avenue-app` > IAM & Admin > **Service accounts** > Create `edit-heartbeat`. Give it the role **Cloud Datastore User** (`roles/datastore.user`) and nothing else.
2. Open it > Keys > Add key > **JSON**, and save the file somewhere temporary. Step 5 puts it into `.env`, and then you delete the file.
3. Add this to the Firestore rules. Anyone can read the status document; no browser can write it (the service account isn't bound by the rules):

   ```
   match /editServer/{doc} {
     allow read: if doc == "status";
     allow write: if false;
   }
   ```

### 5. The server folder and `.env`

In PowerShell on the PC:

```powershell
mkdir C:\latest-avenue-server; cd C:\latest-avenue-server
$raw = "https://raw.githubusercontent.com/metrothenews/latest-avenue-edit/main/cloud/pc"
Invoke-WebRequest "$raw/server.ps1" -OutFile server.ps1
Invoke-WebRequest "$raw/env.example" -OutFile env.example
powershell -ExecutionPolicy Bypass -File server.ps1 -Init -HeartbeatKey $env:USERPROFILE\Downloads\latest-avenue-app-xxxx.json
notepad .env
```

`-Init` fills in:

* the project, owner and origins;
* new random keys for the worker and storage;
* `USE_GPU=1` if it finds the NVIDIA card;
* the heartbeat key, base64-encoded. Delete the JSON file afterwards.

In Notepad, fill in `TUNNEL_TOKEN`, `GHCR_USER` and `GHCR_TOKEN`, then save.

**`.env` is the only place any secret lives on the PC.** Never commit it, send it or paste it anywhere. If one leaks:

* tunnel token: delete the tunnel in Cloudflare and make a new one;
* registry token: revoke it on GitHub;
* service account key: delete it in Google Cloud.

### 6. Swap the old container for the stack

The old container stays, stopped, so you can go back to it:

```powershell
docker stop latest-avenue-edit
docker rename latest-avenue-edit latest-avenue-edit-old
tailscale funnel --https=443 off
powershell -ExecutionPolicy Bypass -File server.ps1
powershell -ExecutionPolicy Bypass -File server.ps1 -ImportData $env:USERPROFILE\latest-avenue-edit-data
```

* The plain run signs in to the registry, pulls `:stable`, takes the stack's files out of it into this folder, and starts everything. It ends with the status line.
* `-ImportData` copies your existing projects, media, renders and models into the stack's data volume. The folder itself is left as it was.

Check from a phone off Wi-Fi: `https://edit-api.latestavenue.com/status`.

**Back to the old container**, if needed:

```powershell
powershell -ExecutionPolicy Bypass -File server.ps1 -Down
docker rename latest-avenue-edit-old latest-avenue-edit
docker start latest-avenue-edit
tailscale funnel --bg --https=443 http://127.0.0.1:8080
```

After a good week, run `docker rm latest-avenue-edit-old`.

### 7. Keep it running

```powershell
powershell -ExecutionPolicy Bypass -File server.ps1 -Install
```

This turns on Docker Desktop's **Start when you sign in**, and sets the PC to never sleep or
hibernate on mains power. The stack starts with Docker by itself. Two more settings, by
hand, so the PC comes back after a power cut:

* **Sign in automatically.** Run `netplwiz` and untick "Users must enter a user name and password". On Windows 11 you may first need to turn off "only allow Windows Hello sign-in" in Settings > Accounts > Sign-in options.
* **Power on after a power cut.** In the BIOS/UEFI, set "Restore on AC power loss" to **Power On**.

That's the last thing done on the PC.

### 8. The website's deploys (latest-avenue repo)

1. Copy [../ci/website-deploy.yml](../ci/website-deploy.yml) into the website repository as `.github/workflows/deploy.yml`. On every push to `main` it runs `npm ci` and `npm run build`, then deploys to Firebase Hosting (live). It needs `firebase.json` in that repository, as for `firebase deploy`.
2. Set up the secret, once. Either way works:
   * **Easiest, on the Mac, in the website repository:** run `npx firebase-tools login`, then `npx firebase-tools init hosting:github`. Pick `latest-avenue-app`. It creates a service account, stores its key as the repository secret `FIREBASE_SERVICE_ACCOUNT_LATEST_AVENUE_APP`, and offers to write workflows. Decline those, or delete them, and keep `deploy.yml`.
   * **By hand:**
     1. In Google Cloud console > IAM > Service accounts, create `github-hosting-deploy` with the roles **Firebase Hosting Admin**, **API Keys Viewer** and **Cloud Run Viewer**.
     2. Keys > Add key > JSON.
     3. In the website repo, go to Settings > Secrets and variables > Actions > New repository secret. Name it `FIREBASE_SERVICE_ACCOUNT_LATEST_AVENUE_APP` and paste the whole JSON.
     4. Delete the file.
3. Push to `main`, or use Actions > Deploy website > Run workflow, and check the run is green.

### 9. The site's own changes (latest-avenue repo, separate work)

* Set `editServiceUrl` (`src/data/edit.js`) to `https://edit-api.latestavenue.com`.
* Before connecting, read `GET /status` (no sign-in, any origin): `{online, version, health, update, queue_depth, sessions_free}`.
* If that fails, read Firestore `editServer/status`. A `lastSeen` more than about 3 minutes old means "the edit server is offline".
* Optionally, add an owner-only admin page on the `/api/admin` calls above.

## What has and hasn't been run

These were run in a Linux container, with stand-ins for Google, and a local registry
standing in for GitHub's. `cloud/test_stack.sh` repeats all of them, and CI runs it:

* Every service healthy, and the public `/status`.
* The website's full `/edit` flow, with the heavy steps on the worker and the outputs in storage.
* The import queue, against a stand-in Drive.
* A worker killed mid-job: the job ran again, and a token job came back `needs_token`.
* A backup.
* The heartbeat, against a stand-in Firestore that checks the service-account signature.
* The admin API, with its owner check, overview, logs, drain and restart.
* Updates:
  * a new release rolled out by itself, and the updater updated itself;
  * a broken release rolled back by itself, with everything healthy again;
  * the owner's rollback went to the version before;
  * the broken `:stable` was not tried again.

These have not been run:

* Windows (`server.ps1`);
* the real Cloudflare tunnel, GitHub registry and Firestore;
* the GitHub Actions workflows themselves (they run once this is merged to `main`);
* the GPU override.
