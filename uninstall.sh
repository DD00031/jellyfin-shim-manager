#!/usr/bin/env bash
# uninstall.sh — one-liner uninstaller for jellyfin-shim-manager.
#
#   curl -fsSL https://raw.githubusercontent.com/DD00031/jellyfin-shim-manager/main/uninstall.sh | bash
#
# By default this only removes jellyfin-shim-manager itself (its systemd
# units, sudoers rule, the pipx-installed CLI, and the cloned source dir).
# Configured jellyfin-mpv-shim instances and their saved logins are left
# running and in place unless you pass --purge-instances. Pass --purge-config
# to also delete /etc/jellyfin-shim-manager (admin credentials, TLS certs).

set -euo pipefail

INSTALL_DIR="${JELLYFIN_SHIM_MANAGER_SRC:-$HOME/.local/src/jellyfin-shim-manager}"
PURGE_INSTANCES=0
PURGE_CONFIG=0
ASSUME_YES=0

for arg in "$@"; do
    case "$arg" in
        --purge-instances) PURGE_INSTANCES=1 ;;
        --purge-config) PURGE_CONFIG=1 ;;
        -y|--yes) ASSUME_YES=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

export PATH="$HOME/.local/bin:$PATH"

if command -v jellyfin-shim-manager >/dev/null 2>&1; then
    CLI_ARGS=()
    [[ "$PURGE_INSTANCES" -eq 1 ]] && CLI_ARGS+=(--purge-instances)
    [[ "$PURGE_CONFIG" -eq 1 ]] && CLI_ARGS+=(--purge-config)
    [[ "$ASSUME_YES" -eq 1 ]] && CLI_ARGS+=(--yes)

    echo "==> Removing systemd units, sudoers rule, and any requested data"
    jellyfin-shim-manager uninstall "${CLI_ARGS[@]}"
else
    echo "jellyfin-shim-manager CLI not found on PATH -- skipping systemd/sudoers cleanup."
fi

if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q '^jellyfin-shim-manager '; then
    echo "==> Removing the CLI (pipx uninstall)"
    pipx uninstall jellyfin-shim-manager
fi

if [[ -d "$INSTALL_DIR" ]]; then
    if [[ "$ASSUME_YES" -eq 1 ]]; then
        confirm=y
    else
        read -rp "Delete the cloned source at $INSTALL_DIR too? [y/N] " confirm
    fi
    if [[ "${confirm,,}" == "y" ]]; then
        rm -rf "$INSTALL_DIR"
        echo "Removed $INSTALL_DIR"
    fi
fi

echo "Done."
