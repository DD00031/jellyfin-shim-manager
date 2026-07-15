#!/usr/bin/env bash
# update.sh — one-liner updater for jellyfin-shim-manager.
#
#   curl -fsSL https://raw.githubusercontent.com/DD00031/jellyfin-shim-manager/main/update.sh | bash
#
# Pulls the latest source, reinstalls the CLI with pipx, and re-runs `setup`
# so any new/changed systemd units get picked up (existing config.json,
# admin credentials, and TLS certs are left alone).

set -euo pipefail

REPO_URL="${JELLYFIN_SHIM_MANAGER_REPO:-https://github.com/DD00031/jellyfin-shim-manager.git}"
INSTALL_DIR="${JELLYFIN_SHIM_MANAGER_SRC:-$HOME/.local/src/jellyfin-shim-manager}"

export PATH="$HOME/.local/bin:$PATH"

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    echo "No existing checkout at $INSTALL_DIR -- cloning fresh instead of updating."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR"
else
    echo "==> Pulling latest changes in $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
fi

if ! command -v pipx >/dev/null 2>&1; then
    echo "pipx not found -- run install.sh first." >&2
    exit 1
fi

echo "==> Reinstalling with pipx"
pipx install --force "$INSTALL_DIR"

echo "==> Refreshing systemd units"
if [[ -r /dev/tty ]]; then
    "$HOME/.local/bin/jellyfin-shim-manager" setup < /dev/tty
else
    "$HOME/.local/bin/jellyfin-shim-manager" setup </dev/null
fi

echo "Update complete."
