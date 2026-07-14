"""jellyfin-shim-manager monitor — drives the Pi's framebuffer status screen.

Shows one of a few static images depending on overall system state (no
Jellyfin service running, no internet, server unreachable, someone playing,
or idle/waiting for a QR scan). Intended to run as a long-lived foreground
loop (e.g. under a systemd service or a getty autologin script).
"""

import subprocess
import time
from pathlib import Path

from . import instances as inst

STATES = ("no_jellyfin", "no_internet", "no_server", "playing", "idle")

IMAGE_FILENAMES = {
    "no_jellyfin": "jellyfin-error.png",
    "no_internet": "no-network.png",
    "no_server": "server-down.png",
    "playing": "ready.png",
    "idle": "join-qr.png",
}


def _ping_internet() -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", "2", "1.1.1.1"],
        capture_output=True,
    )
    return result.returncode == 0


def _server_healthy(host: str, port: int) -> bool:
    result = subprocess.run(
        ["curl", "-s", "--connect-timeout", "2", f"http://{host}:{port}/health"],
        capture_output=True, text=True,
    )
    return "Healthy" in result.stdout


def compute_state(cfg: dict) -> str:
    names = inst.list_instance_names(cfg)

    any_active = False
    any_login_error = False
    any_playing = False

    for user in names:
        if inst.is_service_active(cfg, user):
            any_active = True

        log = inst.recent_journal(cfg, user, lines=20)
        if "Client is not actually connected" in log or "EOFError: EOF when reading a line" in log:
            any_login_error = True

        if inst.instance_playback_state(cfg, user) == "playing":
            any_playing = True
            inst.touch_activity(cfg, user)

    if not names or not any_active or any_login_error:
        return "no_jellyfin"

    if not _ping_internet():
        return "no_internet"

    server_ok = _server_healthy(cfg["local_ip"], cfg["jellyfin_port"])
    if not server_ok and cfg.get("tailscale_ip"):
        server_ok = _server_healthy(cfg["tailscale_ip"], cfg["jellyfin_port"])

    if not server_ok:
        return "no_server"
    if any_playing:
        return "playing"
    return "idle"


def show_image(cfg: dict, state: str):
    subprocess.run(["killall", "-15", "fbi"], capture_output=True)
    image_path = Path(cfg["image_dir"]) / IMAGE_FILENAMES[state]
    subprocess.Popen(
        ["fbi", "-T", "1", "-noverbose", "-a", str(image_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run(cfg: dict):
    current_state = None
    while True:
        new_state = compute_state(cfg)
        if new_state != current_state:
            current_state = new_state
            show_image(cfg, current_state)
        time.sleep(cfg["monitor_poll_seconds"])
