#!/usr/bin/env python3
"""Install the latest Extinction Protocol Linux release without touching saves."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.error import URLError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )
    from rich.table import Table
    from rich.text import Text
except ModuleNotFoundError as error:
    if error.name != "rich":
        raise
    sys.exit(
        "Missing dependency: Rich. Run the setup helper from this checkout:\n"
        "  bash install.sh"
    )


GAME_REPO = "actualraptor/extinction-protocol"
APP_ID = "extinction-protocol"
APP_NAME = "Extinction Protocol"
IMAGE_NAME = "Extinction-Protocol.AppImage"
LOGO_PATH = "native/assets/branding/extinction-protocol-logo.png"
console = Console(highlight=False)
error_console = Console(stderr=True, highlight=False)


class InstallError(Exception):
    """An actionable installation failure."""


def show_summary(title, rows, style="cyan"):
    table = Table.grid(padding=(0, 2), expand=True)
    table.add_column(style="dim", no_wrap=True)
    table.add_column(overflow="fold")
    for label, value in rows:
        table.add_row(Text(label), Text(str(value)))
    console.print(Panel(table, title=title, border_style=style, padding=(1, 2)))


def request(url):
    headers = {"User-Agent": "extinction-protocol-installer"}
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return urlopen(Request(url, headers=headers), timeout=60)


def latest_release(repo):
    with request(f"https://api.github.com/repos/{repo}/releases/latest") as response:
        release = json.load(response)
    if not isinstance(release.get("tag_name"), str) or not isinstance(
        release.get("assets"), list
    ):
        raise InstallError(f"Invalid GitHub release metadata for {repo}.")
    return release


def select_game_asset(assets):
    matches = []
    for asset in assets:
        name = asset["name"]
        normalized = re.sub(r"[-_ ]", "", name).lower()
        if (
            "extinctionprotocol" in normalized
            and "linux" in normalized
            and name.lower().endswith(".tar.gz")
            and not re.search(r"(aarch64|arm64|armhf|i[3-6]86)", normalized)
        ):
            matches.append(asset)
    if len(matches) != 1:
        names = ", ".join(asset["name"] for asset in matches) or "none"
        raise InstallError(f"Expected one Linux x86-64 .tar.gz asset; found: {names}.")
    return matches[0]


def exact_asset(release, name):
    matches = [asset for asset in release["assets"] if asset["name"] == name]
    if len(matches) != 1:
        raise InstallError(f"Release must contain exactly one {name}.")
    return matches[0]


def asset_checksum(asset, release=None):
    digest = asset.get("digest") or ""
    if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        return digest[7:].lower()
    if release is not None:
        sums = exact_asset(release, "SHA256SUMS.txt")
        with request(sums["browser_download_url"]) as response:
            text = response.read().decode("utf-8")
        for line in text.splitlines():
            match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
            if match and match[2] == asset["name"]:
                return match[1].lower()
    raise InstallError(f"No SHA-256 checksum available for {asset['name']}.")


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download(url, destination, checksum=None):
    name = unquote(url.rsplit("/", 1)[-1])
    if destination.is_file():
        with console.status("Checking cached download...", spinner="dots"):
            valid = checksum is None or sha256(destination) == checksum
        if valid:
            console.print(Text(f"Using cached download: {name}", style="dim"))
            return destination
        error_console.print(
            Text(f"Cached checksum mismatch; downloading again: {name}", style="yellow")
        )
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    console.print(Text(name, style="bold"))
    # Unique partial files allow separate installations to share the cache.
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as target:
        partial = Path(target.name)
        try:
            with request(url) as response:
                length = response.headers.get("Content-Length")
                total = int(length) if length is not None else None
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold cyan]{task.description}"),
                    BarColumn(),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("Downloading", total=total)
                    received = 0
                    while chunk := response.read(1024 * 1024):
                        target.write(chunk)
                        received += len(chunk)
                        progress.update(task, advance=len(chunk))
                    if total is not None and received != total:
                        raise InstallError(f"Incomplete download for {name}: {received} of {total} bytes.")
                    progress.update(task, total=received, completed=received)
            target.close()
            if checksum is not None:
                with console.status("Verifying SHA-256...", spinner="dots"):
                    if sha256(partial) != checksum:
                        raise InstallError(f"SHA-256 mismatch for {name}.")
                console.print("[green]SHA-256 verified[/green]")
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
    return destination


def download_asset(asset, cache, checksum):
    return download(asset["browser_download_url"], cache / checksum, checksum)


def extract_game(archive, destination):
    destination.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        for member in members:
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not (member.isfile() or member.isdir())
            ):
                raise InstallError(f"Unsafe archive entry: {member.name}")
            # Godot's self-contained marker would move saves into the AppImage.
            if path.name in {"_sc_", "._sc_"}:
                raise InstallError("Archive enables Godot self-contained mode; refusing to relocate saves.")
        source.extractall(destination, members=members, filter="data")
    candidates = []
    for path in destination.rglob("*"):
        if not path.is_file():
            continue
        name = re.sub(r"[-_ .]", "", path.name).lower()
        if "extinctionprotocol" not in name or path.suffix.lower() in {".so", ".pck"}:
            continue
        with path.open("rb") as binary:
            header = binary.read(20)
        if header[:4] == b"\x7fELF":
            if len(header) < 20 or header[4:6] != b"\x02\x01" or header[18:20] != b"\x3e\x00":
                raise InstallError(f"Not an x86-64 Linux executable: {path.name}")
            candidates.append(path)
    if len(candidates) != 1:
        raise InstallError(f"Expected one game executable; found {len(candidates)}.")
    candidates[0].chmod(0o755)
    return candidates[0]


def desktop_value(value):
    return (
        str(value).replace("\\", "\\\\").replace("\n", "\\n")
        .replace("\r", "\\r").replace("\t", "\\t")
    )


def desktop_exec(path):
    escaped = str(path).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        escaped = escaped.replace(char, "\\" + char)
    return desktop_value('"' + escaped + '"')


def desktop_entry(exec_value, icon):
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Prehistoric survivor roguelite\n"
        f"Exec={exec_value}\n"
        f"Icon={desktop_value(icon)}\n"
        "Terminal=false\n"
        "Categories=Game;ActionGame;\n"
        "StartupNotify=false\n"
    )


def prepare_appdir(archive, logo, appdir):
    game_dir = appdir / "usr/lib" / APP_ID
    executable = extract_game(archive, game_dir)
    relative_dir = executable.parent.relative_to(game_dir)
    apprun = appdir / "AppRun"
    apprun.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'APPDIR=${APPDIR:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}\n'
        f'cd "$APPDIR/usr/lib/{APP_ID}"/{shlex.quote(str(relative_dir))}\n'
        f'exec {shlex.quote("./" + executable.name)} --fullscreen "$@"\n',
        encoding="utf-8",
    )
    apprun.chmod(0o755)
    shutil.copyfile(logo, appdir / f"{APP_ID}.png")
    (appdir / ".DirIcon").symlink_to(f"{APP_ID}.png")
    (appdir / f"{APP_ID}.desktop").write_text(
        desktop_entry("AppRun", APP_ID), encoding="utf-8"
    )


def build_appimage(archive, logo, work, cache):
    with console.status("Resolving AppImage build tools...", spinner="dots"):
        tool_release = latest_release("AppImage/appimagetool")
        runtime_release = latest_release("AppImage/type2-runtime")
    tool_asset = exact_asset(tool_release, "appimagetool-x86_64.AppImage")
    runtime_asset = exact_asset(runtime_release, "runtime-x86_64")
    tool = download_asset(tool_asset, cache, asset_checksum(tool_asset))
    runtime = download_asset(runtime_asset, cache, asset_checksum(runtime_asset))
    tool.chmod(0o755)
    appdir = work / "Extinction-Protocol.AppDir"
    console.print("\n[bold cyan]Package[/bold cyan]  Preparing the game and fullscreen launcher")
    with console.status("Extracting and preparing AppDir...", spinner="dots"):
        prepare_appdir(archive, logo, appdir)
    image = work / IMAGE_NAME
    console.print("[bold cyan]Build[/bold cyan]    Compressing AppImage (this can take a moment)")
    with console.status("Building AppImage...", spinner="dots"):
        try:
            subprocess.run(
                [
                    str(tool), "--appimage-extract-and-run", "--no-appstream",
                    "--runtime-file", str(runtime), str(appdir), str(image),
                ],
                check=True,
                cwd=work,
                env={**os.environ, "ARCH": "x86_64"},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
        except subprocess.CalledProcessError as error:
            raise InstallError(
                f"AppImage build failed (exit {error.returncode}).\n\n"
                f"{error.stdout or 'No build diagnostics were emitted.'}"
            ) from error
    with image.open("rb") as result:
        header = result.read(11)
    if header[:4] != b"\x7fELF" or header[8:11] != b"AI\x02":
        raise InstallError("appimagetool did not produce a type-2 AppImage.")
    image.chmod(0o755)
    return image


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
        partial = Path(target.name)
        try:
            target.write(data)
            target.close()
            partial.chmod(0o644)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)


def xdg_path(variable, fallback):
    value = os.environ.get(variable)
    if value and not Path(value).is_absolute():
        raise InstallError(f"{variable} must be an absolute path.")
    return Path(value) if value else Path.home() / fallback


def install(args):
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise InstallError("The upstream game currently requires x86-64 Linux.")
    if os.geteuid() == 0:
        raise InstallError("Run this installer as your desktop user, not with sudo.")
    install_dir = args.install_dir.expanduser().resolve()
    data_dir = xdg_path("XDG_DATA_HOME", ".local/share")
    cache = xdg_path("XDG_CACHE_HOME", ".cache") / APP_ID
    console.print(Panel(
        Text.assemble((APP_NAME, "bold cyan"), "\nLinux AppImage installer"),
        border_style="cyan", padding=(1, 2),
    ))
    with console.status("Finding the latest GitHub release...", spinner="dots"):
        release = latest_release(GAME_REPO)
        asset = select_game_asset(release["assets"])
        checksum = asset_checksum(asset, release)
    identity = {
        "tag": release["tag_name"],
        "asset": asset["name"],
        "sha256": checksum,
    }
    show_summary("Latest GitHub release", [
        ("Version", identity["tag"]),
        ("Asset", asset["name"]),
        ("Destination", install_dir / IMAGE_NAME),
    ])
    if args.check:
        console.print("[dim]Check only; no files were changed.[/dim]")
        return
    install_dir.mkdir(parents=True, exist_ok=True)
    with (install_dir / f".{APP_ID}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallError("Another installer is using this install directory.") from error
        image = install_dir / IMAGE_NAME
        state_path = install_dir / f".{APP_ID}.json"
        state = None
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError) as error:
                raise InstallError(f"Invalid install metadata at {state_path}: {error}") from error
        current = state == identity and image.is_file() and os.access(image, os.X_OK)
        logo_url = (
            f"https://raw.githubusercontent.com/{GAME_REPO}/"
            f"{quote(release['tag_name'], safe='')}/{LOGO_PATH}"
        )
        logo = download(logo_url, cache / f"logo-{hashlib.sha256(logo_url.encode()).hexdigest()}.png")
        with logo.open("rb") as source:
            if source.read(8) != b"\x89PNG\r\n\x1a\n":
                logo.unlink()
                raise InstallError("The upstream logo is not a PNG image.")
        if not current or args.force:
            archive = download_asset(asset, cache, checksum)
            # Staging on the destination filesystem makes replacement atomic.
            with tempfile.TemporaryDirectory(prefix=f".{APP_ID}-", dir=install_dir) as temporary:
                built_image = build_appimage(archive, logo, Path(temporary), cache)
                built_image.replace(image)
            atomic_write(state_path, (json.dumps(identity, indent=2) + "\n").encode())
        icon = data_dir / "icons" / f"{APP_ID}.png"
        desktop = data_dir / "applications" / f"{APP_ID}.desktop"
        atomic_write(icon, logo.read_bytes())
        atomic_write(desktop, desktop_entry(desktop_exec(image), icon).encode())
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", str(desktop.parent)], check=True)
        console.print()
        show_summary(
            "Already up to date" if current and not args.force else "Ready to play",
            [
                ("Version", identity["tag"]),
                ("AppImage", image),
                ("Menu entry", desktop),
                ("Launch mode", "Fullscreen"),
                ("Save data", "Preserved - existing Godot saves and settings are untouched"),
            ],
            style="green",
        )
        console.print(f"[bold green]Launch {APP_NAME} from your application menu.[/bold green]")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-dir", type=Path, default=Path.home() / "Applications",
        help="AppImage directory (default: ~/Applications)",
    )
    parser.add_argument("--check", action="store_true", help="show the latest release without installing")
    parser.add_argument("--force", action="store_true", help="rebuild even if already up to date")
    args = parser.parse_args(argv)
    try:
        install(args)
    except (InstallError, OSError, URLError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        error_console.print(Panel(Text(str(error)), title="Installation failed", border_style="red"))
        return 1
    except KeyboardInterrupt:
        error_console.print("\n[yellow]Installation interrupted.[/yellow]")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
