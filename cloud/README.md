# Cloud editor

Runs latest avenue video in Google Cloud with no screen attached and shows it live at
`https://latestavenue.com/edit`. It costs nothing while nobody is connected, and only the
site's owner account can start or use it.

```
browser: latestavenue.com/edit            (latest-avenue repo: src/pages/Edit.jsx)
  │  Google sign-in (Firebase), owner only
  │  HTTPS + WebSocket, every request carries the sign-in token
  ▼
Cloud Run service, 0 or 1 instance        (this folder)
  gateway.py     the only public port; verifies the token on every request
  editor.sh      started by the gateway after the first verified request:
    Xvnc           virtual display + VNC server, 127.0.0.1 only
    the editor     the normal app on that display, drawn on the CPU (no GPU)
    control server the editor's tools, 127.0.0.1 only
  /data          Cloud Storage bucket: projects, media, renders
```

To use **your own Windows PC** as the server instead of Google Cloud, see [pc/README.md](pc/README.md): the same container, run by Docker Desktop.

## Who can use it

The page at `/edit` is hidden from everyone but the owner, but that is only the browser.
What protects the compute is `gateway.py`, which runs on the server and checks, on every
request, that it carries a Firebase sign-in token that is:

- signed by Google (checked against Google's published keys),
- issued for this Firebase project, not expired,
- for a user ID in `ALLOWED_UIDS` (the site's `ownerUid`),
- sent from one of the site's own addresses (`ALLOWED_ORIGINS`).

Anything else gets `401` with no detail. The editor is not even started until a request has
passed that check, so other people's traffic can't make it run. An open stream is closed
when its token expires (within the hour); the page reconnects with a fresh one, which fails
if access has been removed. The VNC server and the editor's control server are never
reachable from outside; only the gateway's port is published.

`tests/vse/test_cloud_gateway.py` checks all of this (forged, expired, wrong user, wrong
project, wrong website, unsigned and wrongly signed tokens, token in the URL, the editor's
internal routes) and that the owner gets through.

## Cost

Cloud Run bills per second while an instance is handling requests, and an open stream counts
as a request. With nobody connected the service scales to zero. `--max-instances 1` means
there is never more than one editor running, whatever happens.

- Default size is 4 vCPU and 4 GiB. At Cloud Run's published per-second rates that was about
  $0.38 per hour of use when this was written; check the current price list.
- Idle and connected, the editor used about 1.5% of the CPU and 360 MB in testing, so a
  smaller size (`CPU=2 cloud/deploy.sh`) may be enough; playback and rendering are what need
  the cores, and that has not been measured on Cloud Run.
- Stored files and the image cost a few cents per GB per month.
- Requests that fail sign-in are answered by the gateway alone in a few milliseconds.
  Set a budget alert on the project anyway.

## Deploy or update

Needs the `gcloud` CLI signed in to an account that owns the `latest-avenue-app` project, and
git access to this repository.

```bash
cloud/deploy.sh
```

The Linux build of the editor is fetched from this repository's `linux-build` branch, where it
is kept in two parts (GitHub refuses files over 100 MB; uploading to the release was not
possible from where it was built). To use another build file:

```bash
APP_TARBALL=~/Downloads/latest-avenue-video-linux-x64.tar.xz cloud/deploy.sh
```

It prints the gateway's address. Put that in `editServiceUrl` in `src/data/edit.js` of the
`latest-avenue` repo, then build and deploy the site. Run `cloud/deploy.sh` again to update:
it lays this checkout's Python (`scripts/modules/vse_standalone` and friends) over the Linux
build, so changes to the game view, tools or control server go out without recompiling.
C/C++ changes need a new Linux build (`latest-avenue-video-linux-x64.tar.xz`, a tar of the
build's `bin` folder named `app`) put on the `linux-build` branch; its README says how.

Settings are environment variables at the top of `deploy.sh` (project, region, size, allowed
users, screen size).

## Files

Footage goes into the bucket, under `media/`; it appears in the editor at `/data/media`.
Renders written to `/data/renders` appear in the bucket. From a computer:

```bash
gcloud storage cp -r ./footage gs://latest-avenue-app-edit-data/media/
```

The project is saved to `/data/projects/autosave.blend` every two minutes while connected,
when the last viewer leaves, and when the instance is stopped; it is reopened on the next
start. A project saved under its own name is saved in place instead.

## Run it locally

Put `latest-avenue-video-linux-x64.tar.xz` (from the `linux-build` branch, parts joined) next
to the repository's README, then from the repository root:

```bash
docker build -f cloud/Dockerfile -t latest-avenue-edit .
```

`tests/vse/test_cloud_gateway.py` explains how to run the gateway with test certificates
(`GATEWAY_CERTS_FILE`), which is how it is tested without real Google accounts. Never set
that variable in a real deployment: it replaces Google's keys.

## Limits of this first version

- **No sound.** VNC carries picture and input only. Audio needs a second channel
  (the editor's audio to the browser over WebRTC or a WebSocket); not built.
- **Drawn on the CPU.** Fine for the interface and for stepping through clips; playback of
  high-resolution footage will be slower than on a computer with a graphics card.
- **Fixed screen size** (1600×900 by default), scaled to fit the browser. On a phone in
  portrait the editor is small; the page adds on-screen keys for the game view.
- **One editor, shared.** Two browsers signed in as the owner see and control the same
  session.
- **Uploads go through the bucket**, not the page.

## What has and hasn't been run

Run, on Linux in Docker: the image builds; the gateway's security test passes (38 checks);
the editor starts only after a verified request (3 s); the site's `/edit` page connects,
shows the editor and passes keyboard, mouse and touch through, on a desktop and a phone-sized
screen, against the container (using test certificates in place of Google's); work survives
stopping and restarting the container.

**Not run:** `deploy.sh` itself (no Google Cloud access from where this was built), so the
Cloud Run settings, the bucket mount and its file permissions, real Firebase tokens, and
speed on Cloud Run's processors are all untested. Expect to fix something on the first deploy.
