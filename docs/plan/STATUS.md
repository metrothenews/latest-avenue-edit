# Status

Updated by the documentation worker from the builder's pushed commits (all branches). Last
checked: 2026-11-18, against `origin/main` at `dcfe4331`.

## What works now (before this plan)

From the README's status table, which has the details and caveats:

- The editor (Blender 5.3 VSE, 3D switched off) builds and runs on Linux and the Intel Mac;
  v0.1.0 released with the Intel Mac file. Apple Silicon and Windows builds never run.
- Editor tools in `scripts/modules/vse_standalone/api.py`, served by the control server
  (`POST /rpc`, 127.0.0.1:8792), the MCP bridge (`tools/vse_mcp/vse_mcp.py`) and the
  gateway (`POST /api/rpc`). Includes add/trim/split/ripple tools, LUTs, render, and
  `export_otio` (depends on the build having the OTIO operator).
- Game view (keyboard-driven passes, LUT colour pass, crop zoom).
- Lyric video tools with word-timed transcription via faster-whisper (`tools/vse_lyrics/`).
- Server container running on Sam's Windows PC (Docker, Tailscale Funnel); real Google
  sign-in from the site not yet done.
- A chat-based editing assistant (`cloud/agent.py`) tested on Linux with a small model only.

## The plan in [PRODUCT.md](PRODUCT.md)

Nothing built yet. No builder commits for the assembly-cut pipeline have been pushed to any
branch so far (branches seen: `main`, `linux-build`, `docs/dev-plan`).

## Half-built

Nothing for the plan.

## Next

Roadmap step 1: script parsing (PDF → screenplay JSON), command line.
