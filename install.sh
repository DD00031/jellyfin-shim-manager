#!/usr/bin/env bash
# install.sh — one-liner installer for jellyfin-shim-manager.
#
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh | bash
#
# Installs OS dependencies, pipx-installs the jellyfin-shim-manager CLI so it's
# on PATH as `jellyfin-shim-manager`, and (unless --no-setup is passed) runs
# `jellyfin-shim-manager setup` to install the systemd units, sudoers rule,
# and default config file.

set -euo pipefail

REPO_URL="${JELLYFIN_SHIM_MANAGER_REPO:-https://github.com/DD00031/jellyfin-shim-manager.git}"
INSTALL_DIR="${JELLYFIN_SHIM_MANAGER_SRC:-$HOME/.local/src/jellyfin-shim-manager}"
RUN_SETUP=1

for arg in "$@"; do
    case "$arg" in
        --no-setup) RUN_SETUP=0 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

echo "==> Checking dependencies"
if ! command -v git >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    echo "Installing git, python3, pip, jq, qrencode via apt..."
    sudo apt update
    sudo apt install -y git python3 python3-pip python3-venv jq qrencode
fi

if ! command -v pipx >/dev/null 2>&1; then
    echo "==> Installing pipx"
    sudo apt install -y pipx || python3 -m pip install --user pipx --break-system-packages
    python3 -m pipx ensurepath
fi

echo "==> Fetching jellyfin-shim-manager source"
if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" pull --ff-only
else
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

echo "==> Installing the jellyfin-shim-manager CLI with pipx"
pipx install --force "$INSTALL_DIR"

# pipx installs to ~/.local/bin; make sure it's usable right away in this shell.
export PATH="$HOME/.local/bin:$PATH"

if ! command -v jellyfin-shim-manager >/dev/null 2>&1; then
    echo
    echo "jellyfin-shim-manager was installed but isn't on PATH yet."
    echo "Open a new shell, or run: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

if [[ "$RUN_SETUP" -eq 1 ]]; then
    echo "==> Running jellyfin-shim-manager setup (installs systemd units, sudoers rule, config)"
    "$HOME/.local/bin/jellyfin-shim-manager" setup
else
    echo "==> Skipping setup (--no-setup given). Run 'jellyfin-shim-manager setup' when ready."
fi

cat <<'EOF'

Done!

Next steps:
  1. Edit /etc/jellyfin-shim-manager/config.json (server URL, LAN IP, etc.)
  2. jellyfin-shim-manager add <username>       # log in a permanent instance
  3. jellyfin-shim-manager list                 # see configured instances
  4. jellyfin-shim-manager join                 # (already running as a service) QR onboarding page

See the README for the full command reference.
EOF
