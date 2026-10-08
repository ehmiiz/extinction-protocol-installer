#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

main() {
    local repository_url="https://github.com/ehmiiz/extinction-protocol-installer.git"
    local data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
    local state_dir source_dir="" script_path="${BASH_SOURCE[0]:-}" venv

    [[ "$EUID" -ne 0 ]] || fail "Run this installer as your desktop user, not with sudo."
    [[ "$(uname -sm)" == "Linux x86_64" ]] || fail "The game requires x86-64 Linux."
    [[ "$data_home" == /* ]] || fail "XDG_DATA_HOME must be an absolute path."
    command -v python3 >/dev/null || fail "Python is missing. On Arch: sudo pacman -S --needed python"
    command -v flock >/dev/null || fail "flock is missing. On Arch: sudo pacman -S --needed util-linux"
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
        fail "Python 3.12 or newer is required. Update Python before continuing."

    state_dir="$data_home/extinction-protocol-installer"
    mkdir -p -- "$state_dir"
    exec 9>"$state_dir/bootstrap.lock"
    flock -n 9 || fail "Another installer is already running."

    # A local checkout uses its working tree; stdin/downloaded scripts fetch a managed copy.
    if [[ -n "$script_path" && -f "$script_path" ]]; then
        source_dir="$(cd -- "$(dirname -- "$script_path")" && pwd)"
        if [[ ! -f "$source_dir/install.py" || ! -f "$source_dir/requirements.txt" ]]; then
            source_dir=""
        fi
    fi
    if [[ -z "$source_dir" ]]; then
        command -v git >/dev/null || fail "Git is missing. On Arch: sudo pacman -S --needed git"
        source_dir="$state_dir/source"
        if [[ -e "$source_dir" ]]; then
            [[ -d "$source_dir/.git" ]] || fail "Not an installer checkout: $source_dir"
            [[ "$(git -C "$source_dir" remote get-url origin)" == "$repository_url" ]] ||
                fail "Unexpected repository at $source_dir; refusing to change it."
            git -C "$source_dir" diff --quiet &&
                git -C "$source_dir" diff --cached --quiet ||
                fail "The managed installer has local edits at $source_dir; preserving them."
            printf 'Updating the installer...\n'
            git -C "$source_dir" pull --ff-only --quiet origin main ||
                fail "Could not update the installer. Check your connection and retry."
        else
            printf 'Downloading the installer...\n'
            git clone --quiet --depth 1 --branch main "$repository_url" "$source_dir" ||
                fail "Could not download the installer. Check your connection and retry."
        fi
    fi

    venv="$source_dir/.venv"
    if [[ ! -x "$venv/bin/python" ]]; then
        printf 'Creating an isolated Python environment...\n'
        python3 -m venv "$venv" ||
            fail "Could not create the Python environment. Ensure Python's venv support is installed."
    fi
    printf 'Preparing the terminal interface...\n'
    "$venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$source_dir/requirements.txt" ||
        fail "Could not install Python dependencies. Check the error above and retry."
    exec "$venv/bin/python" "$source_dir/install.py" "$@"
}

main "$@"
