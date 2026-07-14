"""Configuration for jellyfin-shim-manager.

All the values that used to be hardcoded at the top of the various shell
scripts (LOCAL_IP, CONFIG_BASE, ...) now live in a single JSON file so the
whole tool can be installed and configured without editing source.
"""

import json
import os
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("JELLYFIN_SHIM_MANAGER_CONFIG", "/etc/jellyfin-shim-manager/config.json")
)

DEFAULTS = {
    # Where each user's conf.json / cred.json / meta.json lives.
    "config_base": "/home/pi/mpv-shim-configs",
    # Linux user the jellyfin-mpv-shim@ systemd services run as.
    "run_as_user": "pi",
    # Template unit name prefix, e.g. jellyfin-mpv-shim@alice
    "service_prefix": "jellyfin-mpv-shim@",
    "display": ":0",
    # Jellyfin server.
    "jellyfin_url": "http://192.168.2.14:8096",
    "jellyfin_port": 8096,
    "local_ip": "192.168.2.14",
    "tailscale_ip": "",
    # Join web app.
    "bind_host": "0.0.0.0",
    "bind_port": 5005,
    "pending_timeout_seconds": 600,
    "code_regex": r"\b(\d{6})\b",
    # Status monitor (Pi framebuffer screen).
    "image_dir": "/home/pi/Resources",
    "monitor_poll_seconds": 10,
    # Reaper (expires temporary logins).
    "temporary_timeout_seconds": 10800,
}


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
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if overrides:
        cfg.update(overrides)
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    return path
