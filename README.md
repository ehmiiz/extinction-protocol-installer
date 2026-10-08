# Extinction Protocol installer

Install the latest [Extinction Protocol](https://github.com/actualraptor/extinction-protocol/releases/latest)
on **Omarchy / Arch Linux (x86-64)**.

Adds an AppImage and application-menu icon,
launches in fullscreen, and leaves your saves untouched.

## Getting Started

Paste this into your terminal:

```sh
bash -o pipefail -c 'curl -fsSL https://raw.githubusercontent.com/ehmiiz/extinction-protocol-installer/main/install.sh | bash'
```

Setup is automatic. Once finished, open **Extinction Protocol** from your
application menu. **Don't run the installer with sudo.**

Requires Bash, curl, Git, Python 3.12+, working graphics drivers, and about 4 GB of
free space. Missing packages on Arch?

```sh
sudo pacman -S --needed curl git python fuse2
```

Prefer to inspect what runs? [Read the setup script](install.sh).
Already cloned this repository? Run `bash install.sh` instead.

## FAQ

**How do I update?**  
Close the game and rerun the Getting Started command. It installs the latest
GitHub release and skips rebuilding if you're already up to date. No background updater.

**Will I lose my saves?**  
No. The installer leaves existing saves and settings alone.

**Does it launch in fullscreen?**  
Yes, from both the application menu and the AppImage directly.

**Where is the game installed?**  
`~/Applications/Extinction-Protocol.AppImage`. Setup files and cached downloads
stay in your user directories; system Python is unchanged.

**Can I check for updates, rebuild, or choose a folder?**  
From a local checkout:

```sh
bash install.sh --check
bash install.sh --force
bash install.sh --install-dir "$HOME/Games"
```

Use the same custom folder on future updates. `--check` prepares the installer if
needed, but doesn't install the game.

**The game won't launch. What now?**  
Run it in a terminal to see the error:

```sh
"$HOME/Applications/Extinction-Protocol.AppImage"
```

For a FUSE error, install `fuse2` or launch without it:

```sh
"$HOME/Applications/Extinction-Protocol.AppImage" --appimage-extract-and-run
```

**How do I uninstall?**  
Open the Omarchy menu (**Super + Alt + Space**), search for **Extinction Protocol**,
press **Delete**, then select **Uninstall** and press **Enter**.
