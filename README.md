# Extinction Protocol installer

Install or update [Extinction Protocol](https://github.com/actualraptor/extinction-protocol/releases/latest)
as an AppImage with its logo and a friendly application-menu entry. Designed for
Omarchy / Arch Linux on **x86-64**, with no root access or Python packages required.

## Install or update

Requires Python **3.12+**, an internet connection, working OpenGL drivers, and
roughly **4 GB of free space** during packaging. On Arch, install Python if needed
with `sudo pacman -S --needed python`. Launching the AppImage normally requires
FUSE 2 (`sudo pacman -S --needed fuse2`).

```sh
git clone https://github.com/ehmiiz/extinction-protocol-installer.git
cd extinction-protocol-installer
python3 install.py
```

Launch **Extinction Protocol** from the application menu. The AppImage starts the
game in **fullscreen** by default, without editing saved settings. Close the game
and run the same command whenever you want to update. There is no background updater.
An unchanged release is not rebuilt; the menu entry and icon are refreshed.

```sh
python3 install.py --check                 # Show latest release; change nothing
python3 install.py --force                 # Rebuild the current release
python3 install.py --install-dir "$HOME/Games"
```

Use the same `--install-dir` on subsequent updates if you choose a custom location.
Do not run the installer with sudo. Optional `GITHUB_TOKEN` or `GH_TOKEN`
environment variables authenticate GitHub API requests if you hit rate limits.

## What it installs

| Location | Purpose |
| --- | --- |
| `~/Applications/Extinction-Protocol.AppImage` | Game, engine and launcher |
| `~/Applications/.extinction-protocol.json` | Installed release identity |
| `~/Applications/.extinction-protocol.lock` | Prevent concurrent updates |
| `~/.local/share/applications/extinction-protocol.desktop` | Application-menu entry |
| `~/.local/share/icons/extinction-protocol.png` | Official game logo |
| `~/.cache/extinction-protocol/` | Reusable downloads |

`XDG_DATA_HOME` and `XDG_CACHE_HOME` override the respective defaults. Downloads
remain cached to make rebuilds faster; old cache files may be removed when the
installer is not running.

The installer queries GitHub's **latest published release**, not a hard-coded
version. It accepts case and separator variations such as
`Extinction-Protocol-Linux-0.14.1-UNVERIFIED.tar.gz` and
`extinctionProtocolLinux-0.14.1.tar.gz`, and refuses ambiguous asset matches.
Only the upstream x86-64 Linux build is supported.

Game downloads are SHA-256 checked against GitHub release metadata (or the
release's `SHA256SUMS.txt`). The installer downloads and checksum-verifies
[appimagetool](https://github.com/AppImage/appimagetool/releases) and its
[runtime](https://github.com/AppImage/type2-runtime/releases) from their latest
GitHub releases, then builds locally without FUSE. The logo comes from the game
repository at the selected release tag. Checksums detect corruption; these are
upstream-provided artifacts, not independently audited or signed by this project.

Unsafe archive paths, links, special files, unexpected executable architectures,
and Godot self-contained markers are rejected. A failed download or build leaves
the existing AppImage in place; a completed build replaces it atomically.
Errors exit nonzero rather than reporting a successful install.

## Saves and settings

The game uses Godot's existing user-data directory:

```text
~/.local/share/godot/app_userdata/Extinction Protocol
```

With `XDG_DATA_HOME` set, this is
`$XDG_DATA_HOME/godot/app_userdata/Extinction Protocol`. The installer never copies,
deletes, relocates, or resets this directory, and the AppImage launcher does not
override `HOME` or the XDG environment. Existing saves from an extracted Linux
release remain available. Back up the entire directory before major game updates:
upstream game releases may migrate save formats when you launch them.

## Troubleshooting and removal

The upstream Linux release may be marked **UNVERIFIED**. Packaging does not
guarantee that the game works on every graphics driver.

For launch diagnostics, run the AppImage in a terminal:

```sh
"$HOME/Applications/Extinction-Protocol.AppImage"
```

Without FUSE, use extraction mode (slower and requires temporary disk space):

```sh
"$HOME/Applications/Extinction-Protocol.AppImage" --appimage-extract-and-run
```

To uninstall, remove the AppImage, its two dotfiles, the desktop entry and icon
listed above. The download cache is optional to keep. **Leave the Godot user-data
directory alone to preserve your progress.**

## Development

The installer uses only Python's standard library. Run the offline tests with:

```sh
python3 -m unittest discover -s tests -v
```

Tests cover asset selection, checksums, archive handling, launch arguments, desktop
entries, updates, failure recovery and save preservation. An actual packaging
smoke test requires downloading the upstream game and AppImage tools; use a
temporary `--install-dir`, `XDG_DATA_HOME` and `XDG_CACHE_HOME` to avoid changing
your normal desktop installation.
