"""Configuration for jellyfin-shim-manager.

All the values that used to be hardcoded at the top of the various shell
scripts (LOCAL_IP, CONFIG_BASE, ...) now live in a single JSON file so the
whole tool can be installed and configured without editing source.
"""

import getpass
import json
import os
import pwd
from pathlib import Path

from . import privileged

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("JELLYFIN_SHIM_MANAGER_CONFIG", "/etc/jellyfin-shim-manager/config.json")
)

# Kept outside config.json (and out of version control / group-readable
# files) since they're secrets, not settings: /etc/jellyfin-shim-manager/admin.json
# (admin username + password hash) and .../secret_key (Flask session signing key).
ADMIN_CREDENTIALS_PATH = Path(
    os.environ.get("JELLYFIN_SHIM_MANAGER_ADMIN_FILE", "/etc/jellyfin-shim-manager/admin.json")
)
SECRET_KEY_PATH = Path(
    os.environ.get("JELLYFIN_SHIM_MANAGER_SECRET_KEY_FILE", "/etc/jellyfin-shim-manager/secret_key")
)
TLS_DIR = Path(
    os.environ.get("JELLYFIN_SHIM_MANAGER_TLS_DIR", "/etc/jellyfin-shim-manager/tls")
)


def invoking_user() -> str:
    """The human behind this process, even when running under `sudo`.

    Prefers $SUDO_USER (the account that ran `sudo jellyfin-shim-manager ...`)
    over the current effective user, so defaults land on the real operator's
    account instead of root.
    """
    return os.environ.get("SUDO_USER") or getpass.getuser()


def home_dir_for(user: str) -> Path:
    try:
        return Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        return Path.home()


def _build_defaults() -> dict:
    user = invoking_user()
    home = home_dir_for(user)
    return {
        # Where each user's conf.json / cred.json / meta.json lives.
        "config_base": str(home / "mpv-shim-configs"),
        # Linux user the jellyfin-mpv-shim@ systemd services run as.
        "run_as_user": user,
        # Template unit name prefix, e.g. jellyfin-mpv-shim@alice
        "service_prefix": "jellyfin-mpv-shim@",
        "display": ":0",
        # Jellyfin server -- there's no sane default; setup prompts for this.
        # NOTE: local_ip is the *Jellyfin server's* LAN IP (used only by
        # monitor.py's health check) -- it is NOT this box's own address.
        # See manager_ip below for that; conflating the two pointed the
        # join QR code at the wrong machine whenever the Pi running
        # jellyfin-shim-manager isn't also the Jellyfin server.
        "jellyfin_url": "",
        "jellyfin_port": 8096,
        "local_ip": "",
        "tailscale_ip": "",
        # This box's own LAN IP -- what other devices use to reach the
        # join/admin web app, e.g. the QR code on the idle status screen.
        # Auto-detected (with a chance to confirm/override) by `setup`.
        "manager_ip": "",
        # Join + admin web app.
        "bind_host": "0.0.0.0",
        "bind_port": 5005,
        "login_timeout_seconds": 45,
        "tls_enabled": False,
        "tls_cert": str(TLS_DIR / "cert.pem"),
        "tls_key": str(TLS_DIR / "key.pem"),
        # Status monitor (Pi framebuffer screen).
        "image_dir": str(home / "Resources"),
        "monitor_poll_seconds": 10,
        # Reaper (expires temporary logins).
        "temporary_timeout_seconds": 10800,
    }


# Computed fresh per-process (each CLI invocation is a new process), so this
# always reflects whoever is actually running the command right now.
DEFAULTS = _build_defaults()


def config_path() -> Path:
    return DEFAULT_CONFIG_PATH


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    path = config_path()
    if path.exists():
        try:
            user_cfg = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Failed to parse {path}: {exc}")
        cfg.update(user_cfg)
    return cfg


def write_default_config(path: Path = None, overrides: dict = None) -> Path:
    path = path or config_path()
    cfg = dict(DEFAULTS)
    if overrides:
        cfg.update(overrides)
    privileged.write_file(path, json.dumps(cfg, indent=2) + "\n", mode=0o644)
    return path
