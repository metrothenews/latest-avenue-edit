# Scaling: from Sam's PC to an online service

> **Guiding principle (Sam): "Build lean and local, then migrate to the cloud later."**
> Everything runs on Sam's PC first, at minimal cost and complexity. Cloud pieces (P2, P3)
> are added only when real users need them. The local design must not block that move: the
> same Docker image, S3-compatible storage, a queue and a stable hostname. Nothing is built
> for the cloud before it is needed.

Sam's decision (2026-11-18): latest avenue edit becomes an **online service**, not an
installed app. For now Sam's PC is a proper test server. It is built so that cloud computers
can be added, more of them as users arrive (horizontal scaling). This page is the plan. None
of it is built yet; see [ROADMAP.md](ROADMAP.md) for status.

## How it fits together

There is one Docker image. Its configuration decides which of the roles below it plays.

1. **Gateway (stateless API).**
   - Checks the Firebase sign-in on every request.
   - Owns projects, quotas and the job queue.
   - Routes each user's live view (WebSocket) to the computer running their session.
   - Sits behind one fixed address, e.g. `edit-api.latestavenue.com`, through Cloudflare
     Tunnel. This replaces the Tailscale Funnel address, so the backend can move between the
     PC and the cloud without changing the site.
2. **Job queue (Redis).**
   - Every heavy step is a job: import, probe/proxy, transcribe, assembly, render, export
     and Drive upload. None of them runs inside the Blender process.
   - Jobs can be safely re-run and resumed. They have retries and per-user priority.
3. **Workers (stateless, same image).**
   - Workers take jobs from the queue and report progress.
   - Each pool of workers is tagged by what it can do (`gpu`, `cpu`) and where it runs
     (`pc`, `cloud`).
   - Scaling up means adding workers. Sam's PC is just one worker in the `pc` pool.
4. **Interactive sessions.** A session is the Blender sequencer plus its noVNC stream.
   - There is one container per active user. It stays tied to that user, stops after a
     time idle, and is saved to storage when it closes.
   - The PC runs 1–2 at once. Later the cloud adds session computers when needed.
   - When everything is full, the user sees a waiting-room message.
5. **Storage.**
   - Media, proxies, renders and exports go in S3-compatible object storage, with a
     separate path per user. That is MinIO on the PC now, and Google Cloud Storage later
     with the same code.
   - Local disk is only a cache.
   - Project and job details are kept in Firestore, which the site already uses.

## Phases

| Phase | What | Main pieces |
|---|---|---|
| **P0** (now) | The PC as a proper test server | Docker Compose with the gateway, Redis, MinIO, a worker and a session. Starts by itself on boot, with restart policies, health checks and an uptime check. Nightly backup of projects. Sleep turned off. Simple logs and metrics. The Cloudflare Tunnel address. Assembly, import and render moved onto the queue. |
| **P1** | Beta for several users, still on the PC | An invite list in Firestore in place of the single `ownerUid`. Storage and quotas per user. A session limit with a waiting room. An admin view of jobs and users. |
| **P2** | PC plus cloud | A pool of cloud GPU workers using the same image, on Google Cloud (e.g. L4 GPU VMs or Cloud Run GPU), because the site is already on Firebase/Google. It takes jobs when the PC is busy or offline. Storage moves to GCS. Budget caps. The PC becomes optional. |
| **P3** | Scale | Workers added or removed by how many jobs are waiting, down to zero when idle. Cloud session computers. The gateway on Cloud Run. Monitoring and alerts. Usage metering (GPU-seconds) and billing (e.g. Stripe). Status shown in `/edit`. |

## Hands-off PC (decided by Sam, part of P0)

After a one-time setup, nobody touches the PC. Updates, merges and deploys all happen from
Sam's Mac or the cloud, and no Claude sessions run on the PC.

- **Editor updates.** GitHub Actions builds and tests the editor image and pushes it to a
  private registry (GHCR). An updater on the PC:
  - pulls the new `:stable` image;
  - restarts the services and health-checks them;
  - rolls back by itself if a check fails.
- **Website.** It deploys to Firebase Hosting from GitHub Actions when changes are merged to
  `main`.
- **Seeing the PC from elsewhere.**
  - `/status`;
  - an admin API only the owner can use (logs, queue, restart);
  - the Firestore heartbeat, which reports the running version.
- **Secrets** stay only in a local env file on the PC.

## When things go wrong

- **The PC goes offline.** The gateway marks the `pc` pool as down. Jobs wait, or go to
  cloud workers once P2 exists. The site shows the offline message (see "Answered by Sam"
  below).
- **Something restarts.** Each job's state lives in Redis/Firestore, so a restart loses
  nothing.
- **Cost.** Each user has a quota, and a monthly cloud budget cap pauses the cloud workers
  when it is reached.

## Licence note

Blender is GPL. The coordinating session's note: running it as a service is fine, but
distributing a modified Blender triggers GPL obligations. This is not legal advice. This
repository already publishes builds of the modified editor (release v0.1.0), and its source
is public.

## Answered by Sam (2026-11-18)

- **Cloudflare.** Use Cloudflare Tunnel for a stable hostname such as
  `edit-api.latestavenue.com`. This needs latestavenue.com's DNS moved to Cloudflare's free
  plan, keeping the Firebase Hosting records.
- **The PC stays on 24/7.** If it goes offline:
  - a public `/status` endpoint reports it, and the server writes a heartbeat to Firestore
    every 60 s;
  - the site shows "The editor server is offline (since …). Your projects are safe; try
    again later.";
  - the parts that don't need the server stay usable (browsing Drive, reading saved editing
    scripts);
  - queued work resumes when the PC restarts.

## Open questions

These are in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md): the monthly cloud budget cap, the size of
the beta, and the pricing model.
