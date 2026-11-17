# Your Windows PC as the server

The editor runs on your PC with no window and is used from `https://latestavenue.com/edit`
in any browser. All the work (decoding, effects, rendering) happens on the PC; the browser
only shows the picture and sends mouse and keys. Only the site's owner account gets in.

```
browser: latestavenue.com/edit  ──https──▶  tunnel address  ──▶  your PC
                                                      127.0.0.1:8080  gateway (sign-in check)
                                                        └─ container: the editor, no window
                                                           /data  =  a folder on the PC
```

## What runs on the PC

The same container as the cloud version (see [../README.md](../README.md)), in Docker
Desktop. It is the Linux build of the editor running on Windows through WSL 2, which Docker
Desktop sets up. This was chosen over a native Windows build because:

- it is the exact image that is tested (security test, browser test, restart test);
- it needs no desktop session: nothing appears on your screen, and it is not showing your
  desktop to the web, only the editor;
- it restarts by itself with Docker.

A native Windows build of the app (for using it at the desk) is separate work and is not
needed for this.

## Set up

1. Install **Docker Desktop** (<https://www.docker.com/products/docker-desktop/>), start it,
   and in its Settings > General turn on **Start Docker Desktop when you sign in**.
2. Install **Git for Windows**, then in a terminal:

   ```
   set GIT_LFS_SKIP_SMUDGE=1
   git clone --depth 1 https://github.com/metrothenews/latest-avenue-edit %USERPROFILE%\latest-avenue-edit
   ```

3. Put `latest-avenue-video-linux-x64.tar.xz` (the editor's Linux build) in your Downloads
   folder, unless it has been uploaded to the GitHub release and you have the `gh` CLI.
4. Install **Tailscale** (<https://tailscale.com/download>) and sign in. It gives the PC a
   fixed `https://…ts.net` address without touching your router. (Or use `-Tunnel none` and
   provide an https address another way; see below.)
5. Double-click `cloud\pc\install_edit_server.cmd`.

It checks Docker, builds the image (a few minutes the first time), starts the server, turns
on the tunnel and prints the PC's address. Put that address in `editServiceUrl` in
`src/data/edit.js` of the `latest-avenue` repo, then build and deploy the site.

**To update, run it again.** Options (from PowerShell):

```
powershell -ExecutionPolicy Bypass -File cloud\pc\install.ps1 -DataDir D:\edit -Cpus 8
```

`-DataDir` where projects, media and renders live (default `%USERPROFILE%\latest-avenue-edit-data`),
`-Cpus` cores the editor may use (default all but two), `-Memory 8g`, `-Port`, `-Tunnel none`,
`-OwnerUids`, `-NoPull`, `-Uninstall` (removes the server, keeps your files).

## Files

Put footage in `<DataDir>\media`; it is at `/data/media` in the editor. Renders written to
`/data/renders` appear in `<DataDir>\renders`. The project autosaves to
`<DataDir>\projects\autosave.blend`.

## Who can reach it

- The gateway listens on `127.0.0.1` only. Nothing on your network or the internet can reach
  it except through the tunnel.
- Through the tunnel, every request must carry your Google sign-in from the site; the
  gateway verifies it with Google's keys and only answers for the owner's user ID. Anything
  else gets a refusal, and the editor is not even started for it.
- The container cannot see your PC's files, except the data folder.

## Keeping it available

The page can only connect while the PC is on, awake, signed in to Windows (Docker Desktop
starts at sign-in) and online. For an always-on server: Settings > System > Power > set
"When plugged in, put my device to sleep after" to **Never**.

While nobody is connected the editor sits idle (about 1.5% of one core and 360 MB in
testing), and before the first connection after a start it is not running at all.

## Another tunnel

Anything that gives `http://127.0.0.1:8080` a public **https** address and passes WebSockets
works: Cloudflare Tunnel (needs the domain's DNS at Cloudflare for a fixed address), ngrok,
or a port forward with your own certificate. Run the installer with `-Tunnel none` and put
that address in `editServiceUrl`. The address must be https: the site is, and browsers
refuse to connect an https page to a plain http one.

## Limits

The same as the cloud version: no sound yet, fixed 1600×900 picture scaled to the browser,
one shared session. The editor draws with the processor, not the graphics card, in this
first version: the container doesn't use the PC's GPU yet. That matters for playback speed
of large footage, less for cutting and color.

## What has and hasn't been run

Run on Linux: the image built from the repository root; the container started the way
`install.ps1` starts it (local-only port, a host folder as `/data`, restart policy); the
security test (38 checks) and the restart test against it; the port refused from the
machine's network address.

**Not run: `install.ps1` and `install_edit_server.cmd` themselves, Docker Desktop on
Windows, Tailscale Funnel, and a real Google sign-in.** They were written without a Windows
PC or PowerShell available. Expect to fix something in the script on the first run; the
container it starts is the tested part.
