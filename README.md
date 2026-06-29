# ipasideloader

A cross-platform, open-source IPA sideloader — install your own signed
.ipa files onto your own iOS devices from Linux, Windows, or macOS, using
your own Apple ID or your own developer certificate.

This project deliberately does **not** reimplement Apple's binary signing
format. Every actual cryptographic / binary-patching operation is done by
an existing, actively-maintained tool, shelled out to as a subprocess:

| What | How |
|---|---|
| Real CMS + CodeDirectory signing (cross-platform) | [`zsign`](https://github.com/zhlynn/zsign) |
| Ad-hoc / fake signing (jailbreak, TrollStore-style) | [`ldid`](https://github.com/ProcursusTeam/ldid) |
| Native Apple signing (macOS only) | the system `codesign` + `security` tools |
| Device pairing, install, provisioning-profile sync | [`pymobiledevice3`](https://github.com/doronz88/pymobiledevice3) |
| Local anisette data (no server needed, when it works) | [`Anisette.py`](https://github.com/malmeloo/Anisette.py) |

This is the same general division of labor Sideloadly, AltServer, and
SideStore use under the hood.

## What's solid vs. what's best-effort

Being upfront about this matters, because not every layer here has the
same level of confidence behind it:

- **Signing backends, IPA unpack/repack, device install/profile sync** —
  these wrap actively-maintained tools/libraries with stable, documented
  APIs. I verified the `pymobiledevice3` calls used here directly against
  that package's real source rather than from memory, and tested the full
  unpack → sign → repack → install pipeline end-to-end with a fake IPA
  and a fake signing binary. This part should be reliable.

- **Apple ID login + free-developer-account App ID/provisioning-profile
  registration** (`ipasideloader/apple/`) — there is currently no
  actively-maintained, batteries-included open-source Python library for
  this. Every sideloading tool, including Sideloadly and AltServer, has
  had to implement Apple's GrandSlam (GSA) auth protocol themselves
  against undocumented endpoints. The implementation here is a faithful
  best-effort based on the protocol's publicly documented shape, but
  Apple can and does change small details over time (field names, 2FA
  flow specifics). **If Apple ID login breaks, this is the module to
  look at first** — capturing real traffic from another sideloading tool
  with a proxy (mitmproxy/Charles) against your own account, then diffing
  against `apple/auth.py` and `apple/developer_services.py`, is the
  reliable way to fix it.

  Signing and installing with a `.p12` + `.mobileprovision` you already
  have (no Apple ID login involved) is unaffected by any of this.

## Docker (recommended — nothing to build/install yourself)

`zsign` has no official prebuilt binaries for any platform (verified —
every distribution channel is "build it yourself"), and getting a
consistent, trusted `ldid` build across three OSes has the same problem.
Rather than ask you to compile C++ on your own machine, the Docker image
builds both from source once, bakes in `pymobiledevice3`, `Anisette.py`,
and the rest of the Python stack, and gives you one image that behaves
identically on Linux, macOS, and Windows.

```bash
docker compose build
mkdir -p work   # put your .ipa / .p12 / .mobileprovision files here

docker compose run --rm ipasideloader backends
docker compose run --rm ipasideloader sign-install /work/MyApp.ipa \
    --profile /work/app.mobileprovision \
    --p12 /work/cert.p12 --p12-password hunter2 \
    --no-install -o /work/MyApp-signed.ipa
```

Everything under `./work` on your host is visible at `/work` inside the
container — that's where you put input files and where signed output
shows up.

### Docker + device connectivity (read this before expecting `--install` to work)

This is a real constraint, not a bug to be coded around: **Docker
Desktop on macOS and Windows runs containers inside a hidden VM and
cannot pass a USB-connected iPhone through to a container.** Your three
practical options:

1. **Sign in Docker, install on the host.** Run `sign-install ... --no-install
   -o /work/signed.ipa`, then install that file using a
   locally-installed `pymobiledevice3` (`pip install pymobiledevice3`,
   then `pymobiledevice3 apps install signed.ipa`) or any tool that can
   install an already-signed IPA (Apple Configurator, Sideloadly itself,
   etc.) directly on your host OS.
2. **WiFi-paired device.** Pair the device for WiFi syncing once via
   Finder (macOS) or iTunes (Windows), then point `--udid` at it — no
   USB needed at that point, so the container can reach it over the
   network the same way it'd reach an anisette server.
3. **Linux only — direct USB passthrough.** Add `/dev/bus/usb` as a
   device to the container (see the commented-out `devices:` line in
   `docker-compose.yml`) and the container's `usbmuxd` can talk to it
   directly.

The signing step itself (the part you actually asked Docker to solve)
works identically and fully inside the container on every platform —
this constraint only affects the final "push it onto a physical phone"
step.

### Windows USB passthrough — getting your iPhone into the container

If Sideloadly/SideStore/AltStore have been failing to connect over USB or
WiFi on Windows, that's very often **not actually a WiFi problem** — it's
the third-party `usbmuxd`/pairing layer those tools bundle on Windows
being flaky, even when Windows itself sees the device fine (check Device
Manager: if your iPhone shows up there with no warning icon, the USB
driver layer is healthy and the problem is one layer up). Running
`usbmuxd` *inside* this project's container sidesteps that flaky layer
entirely — the remaining question is just how to get the raw USB
connection into the container.

Docker Desktop has an **officially documented** way to do this
(<https://docs.docker.com/desktop/features/usbip/>), using its own
built-in USB/IP server — no extra software to install on Windows. The
docs list it as confirmed for the **Hyper-V backend**; whether it also
works on the **WSL2 backend** (the default most people have) is
genuinely unconfirmed — some users report success, Docker's own docs
don't promise it. Rather than guess which one applies to you, two
scripts in `scripts/` tell you directly, without changing anything on
your system:

```powershell
cd scripts
powershell -ExecutionPolicy Bypass -File diagnose.ps1       # read-only check
powershell -ExecutionPolicy Bypass -File usbip-attach.ps1   # tries the real attach, tells you what happened
```

`diagnose.ps1` checks which Docker backend you're on and whether Windows
sees your iPhone at all. `usbip-attach.ps1` then actually attempts the
documented attach flow and reports plainly whether your iPhone's bus ID
shows up — if it does, you attach it and
`docker compose run --rm ipasideloader devices` should see the phone; if
nothing changes in the device list when you plug/unplug the phone,
that's the WSL2 limitation showing up, and the realistic fallback is
signing in the container (`--no-install`) and installing the resulting
`.ipa` from the host, or using a WiFi-paired device.

Neither script modifies Docker Desktop's configuration, installs
anything, or makes permanent changes — they only run commands against a
disposable helper container that you can stop any time.

## Installation without Docker

The Docker path above is the recommended one since it needs nothing
installed locally except Docker itself. If you'd rather run this
natively instead:

```bash
pip install -r requirements.txt
```

Some Linux distros split Tkinter out of the base Python install:

```bash
sudo apt install python3-tk      # Debian/Ubuntu
sudo dnf install python3-tkinter # Fedora
```

### External signing tools

`zsign` has no official prebuilt binaries for any platform — building it
from source (as the Dockerfile does) is the only reliable path; see
https://github.com/zhlynn/zsign for build instructions per OS. `ldid`
does publish prebuilt static Linux binaries on its GitHub Releases page
(https://github.com/ProcursusTeam/ldid/releases) if you'd rather not
build it. Either way, both need to end up on your `PATH` (or you can
pass an explicit path via the GUI/CLI).

On macOS, the `codesign` backend needs no extra install — it uses the
system tools — but does need a real Apple Developer identity already
imported into a Keychain (Xcode or `security import mycert.p12` will do
this for you).

## Usage

### CLI

```bash
# List connected devices
ipasideloader devices

# Check which signing backends are available on this machine
ipasideloader backends

# Sign and install onto a connected device
ipasideloader sign-install MyApp.ipa \
    --profile app.mobileprovision \
    --p12 cert.p12 --p12-password hunter2

# Sign only, don't install, force a specific backend, save output
ipasideloader sign-install MyApp.ipa \
    --profile app.mobileprovision \
    --backend ldid --no-install -o MyApp-signed.ipa
```

### GUI

```bash
ipasideloader-gui
```

Three tabs: **Sideload** (pick an IPA + profile + cert, sign and/or
install), **Devices** (see what's connected), **Settings** (custom
anisette server URL, Apple ID).

### Anisette server resolution order

For flows that need Apple ID authentication, anisette data is resolved
in this order:

1. A custom anisette server URL you set in Settings, if reachable.
2. A local, in-process provider (`Anisette.py`) — no server needed, but
   requires reaching `mikealmel.ooo` once to download library data the
   first time it runs.
3. Known public anisette servers, as a last resort.

## Project layout

```
ipasideloader/
  signing/        zsign / ldid / codesign backends (subprocess wrappers) + manager
  anisette/       custom URL -> local provider -> public servers, in order
  apple/          GSA login (auth.py) + developer-services client (developer_services.py)
  device/         pymobiledevice3-backed device discovery, install, profile sync
  ipa_archive.py  unzip/repack .ipa <-> .app bundle
  pipeline.py     orchestrates unpack -> sign -> repack -> install
  cli.py          argparse-based CLI
  gui/app.py      Tkinter GUI
scripts/
  diagnose.ps1      read-only Windows USB/Docker-backend diagnostic
  usbip-attach.ps1  attempts the documented Docker USB/IP attach flow
```

## Legal note

This tool resigns and installs apps you already have the rights to run —
your own builds, or IPAs you're otherwise entitled to install — using
your own Apple ID or developer certificate. It doesn't bypass Apple's
signing requirements; it goes through the same developer-provisioning
mechanism Xcode and Apple's own tools use.
