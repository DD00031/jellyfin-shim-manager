"""Detects and installs the external tools jellyfin-shim-manager needs.

`jellyfin-mpv-shim` and `mpv` are required to actually play anything; the
rest are optional (only needed for --tls, join-qr.png, or the physical
console status screen).
"""

import shutil
import subprocess
import sys

APT_PACKAGES = {
    "mpv": "mpv",
    "openssl": "openssl",
    "qrencode": "qrencode",
    "fbi": "fbi",
}

REQUIRED = ["mpv", "jellyfin-mpv-shim"]
OPTIONAL = ["openssl", "qrencode", "fbi"]

DESCRIPTIONS = {
    "mpv": "media player jellyfin-mpv-shim drives",
    "jellyfin-mpv-shim": "the shim itself -- casts Jellyfin playback to mpv",
    "openssl": "generates the self-signed cert for `setup --tls`",
    "qrencode": "generates the join-qr.png shown on the idle status screen",
    "fbi": "draws status images to the console framebuffer (`monitor`)",
}


def is_installed(name: str) -> bool:
    return shutil.which(name) is not None


def check_all() -> dict:
    return {name: is_installed(name) for name in REQUIRED + OPTIONAL}


def install_apt_package(pkg: str):
    subprocess.run(["sudo", "apt-get", "install", "-y", pkg], check=True)


def install_jellyfin_mpv_shim():
    # Installed system-wide via pip (not pipx), so the systemd services --
    # which run as run_as_user, not whoever ran `pipx install` -- can find
    # it on the default PATH without extra configuration.
    subprocess.run(
        ["python3", "-m", "pip", "install", "--break-system-packages", "--upgrade", "jellyfin-mpv-shim"],
        check=True,
    )


def install(name: str):
    if name == "jellyfin-mpv-shim":
        install_jellyfin_mpv_shim()
    elif name in APT_PACKAGES:
        install_apt_package(APT_PACKAGES[name])
    else:
        raise ValueError(f"Don't know how to install '{name}'")


def install_missing(names):
    installed = []
    for name in names:
        print(f"Installing {name}...")
        install(name)
        installed.append(name)
    return installed
