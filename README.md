# Linux build of latest avenue video

This branch only holds the editor's Linux x64 build, for the cloud editor and for a PC acting
as the server (see `cloud/README.md` and `cloud/pc/README.md` on `main`). It is here because
the file could not be uploaded to the GitHub release from where it was built. The file is
split in two because GitHub refuses files over 100 MB.

`cloud/pc/install.ps1` and `cloud/deploy.sh` fetch and join it by themselves. By hand:

```bash
cat latest-avenue-video-linux-x64.tar.xz.part00 latest-avenue-video-linux-x64.tar.xz.part01 > latest-avenue-video-linux-x64.tar.xz
sha256sum -c SHA256SUMS
```

Built on Ubuntu 24.04 (GCC 14) from `main`'s C/C++ as of v0.1.0. The Python layer is taken
from the checkout at image build time, so this file only needs replacing when C/C++ changes.
To replace it: put the new parts and `SHA256SUMS` in a single new commit on this branch
(force-push an orphan commit to keep the branch small).
