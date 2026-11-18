# Go live checklist (draft)

Sam's steps to put the app live by noon Eastern, 2026-11-18 (17:00 UTC). Tick each box as
you go. If a step fails, stop and tell a worker what you saw. Don't skip ahead.

Live means: Sam signs in at latestavenue.com/edit, the page reaches the editor on his
Windows PC, and an assembly runs end to end on a small real folder.

## 0. Before you start

- [ ] The builder's work is on the branch the PC installs from. Today it is on
      `claude/assembly-pipeline` in latest-avenue-edit, not on `main`. Ask the builder which
      branch the PC should use, or whether to merge into `main` first.
- [ ] The website's work is ready to merge. Today it is on `claude/great-johnson-glhijj` in
      the latest-avenue repo (`d2b6c96`).
- [ ] Docker Desktop and Tailscale are running on the PC.

## 1. Update the editor on the PC

Follow **[cloud/pc/README.md](../../cloud/pc/README.md)**, which the builder is writing. The
steps live there so there is one copy. It covers updating (run the installer again), the
graphics card (CUDA), the picture model, and the test command.

- [ ] Editor updated and running (the README's health check answers `ok`).
- [ ] The picture model (MobileCLIP2-S0) is installed. Without it, clips are grouped by
      colour only, which was much worse on real footage (59% vs 94% purity).
- [ ] Write down the PC's https address that the installer prints.

## 2. Merge and deploy the site (latest-avenue repo)

- [ ] `editServiceUrl` in `src/data/edit.js` is the PC's address from step 1.
- [ ] Merge `claude/great-johnson-glhijj` into the branch the site deploys from.
- [ ] Build and deploy the site the usual way for latest-avenue. (Ask the design worker for
      the exact command if unsure.)

## 3. Test with a real sign-in

From a phone and from a computer:

- [ ] Open latestavenue.com/edit and sign in with Google as the owner. The page loads, and
      the editor starts (it can take a few seconds on the first request).
- [ ] Signed out, or in a private window, `/edit` shows nothing usable.
- [ ] Pick a small Drive folder (a few clips) and a script or a song. Run the assembly and
      wait for it to finish.
- [ ] Review: takes appear with confidence and sync labels; swap one take.
- [ ] Export a render and one sequence file to Drive. They appear in "latest avenue
      exports".
- [ ] Optional: open the sequence file in Resolve, Premiere or Final Cut. This has never
      been done. Tell a worker if it fails to open.

## Known limits on day one

- Exports pass format checks but have never been opened in Resolve, Premiere or Final Cut.
- The graphics-card path has not been run. If CUDA fails, it falls back to the processor:
  slower, but it works.
- A song around 170 BPM may be cut at half speed.
- The assistant chat is off by default (a setting).
