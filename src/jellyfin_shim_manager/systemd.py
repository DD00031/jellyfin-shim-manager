"""Generates and installs the systemd units and sudoers rule this tool needs.

Three kinds of units are involved:
  - jellyfin-mpv-shim@.service   template unit, one instance per user, started
                                  by `add` / the join web app.
  - jellyfin-shim-manager-join.service       runs `jellyfin-shim-manager join`
  - jellyfin-shim-manager-reaper.service/.timer  periodically runs `jellyfin-shim-manager reap`
"""

import shutil
import subprocess
from pathlib import Path

SYSTEMD_DIR = Path("/etc/systemd/system")
SUDOERS_PATH = Path("/etc/sudoers.d/jellyfin-shim-manager")

SHIM_TEMPLATE_UNIT = "jellyfin-mpv-shim@.service"
JOIN_UNIT = "jellyfin-shim-manager-join.service"
REAPER_SERVICE_UNIT = "jellyfin-shim-manager-reaper.service"
REAPER_TIMER_UNIT = "jellyfin-shim-manager-reaper.timer"


def _shim_template_unit(cfg: dict) -> str:
    return f"""[Unit]
Description=Jellyfin MPV Shim (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={cfg['run_as_user']}
Environment=DISPLAY={cfg['display']}
ExecStart=/usr/bin/jellyfin-mpv-shim --config {cfg['config_base']}/%i
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def _join_unit(cfg: dict, exe: str) -> str:
    return f"""[Unit]
Description=jellyfin-shim-manager onboarding web app (QR join page)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={cfg['run_as_user']}
ExecStart={exe} join
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def _reaper_service_unit(exe: str) -> str:
    return f"""[Unit]
Description=Reap expired temporary jellyfin-mpv-shim instances

[Service]
Type=oneshot
ExecStart={exe} reap
"""


def _reaper_timer_unit() -> str:
    return """[Unit]
Description=Periodically check for expired temporary jellyfin-mpv-shim logins

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
"""


def _sudoers_rule(cfg: dict, run_as_user: str) -> str:
    prefix = cfg["service_prefix"]
    return (
        f"# Managed by jellyfin-shim-manager. Do not run the join app or reaper as root;\n"
        f"# this grants only the narrow systemctl commands they need.\n"
        f"{run_as_user} ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable --now {prefix}*\n"
        f"{run_as_user} ALL=(ALL) NOPASSWD: /usr/bin/systemctl start {prefix}*\n"
        f"{run_as_user} ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable --now {prefix}*\n"
    )


def ensure_shim_template_unit(cfg: dict, force: bool = False) -> bool:
    """Writes jellyfin-mpv-shim@.service if missing (or if force). Returns True if written."""
    path = SYSTEMD_DIR / SHIM_TEMPLATE_UNIT
    if path.exists() and not force:
        return False
    _write_unit(path, _shim_template_unit(cfg))
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    return True


def install_manager_units(cfg: dict, exe: str = None, enable: bool = True):
    """Installs the join-app and reaper units, plus the shim template unit."""
    exe = exe or shutil.which("jellyfin-shim-manager") or "/usr/local/bin/jellyfin-shim-manager"

    ensure_shim_template_unit(cfg)
    _write_unit(SYSTEMD_DIR / JOIN_UNIT, _join_unit(cfg, exe))
    _write_unit(SYSTEMD_DIR / REAPER_SERVICE_UNIT, _reaper_service_unit(exe))
    _write_unit(SYSTEMD_DIR / REAPER_TIMER_UNIT, _reaper_timer_unit())

    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)

    if enable:
        subprocess.run(["sudo", "systemctl", "enable", "--now", REAPER_TIMER_UNIT], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "--now", JOIN_UNIT], check=True)


def install_sudoers_rule(cfg: dict, run_as_user: str = None):
    run_as_user = run_as_user or cfg["run_as_user"]
    content = _sudoers_rule(cfg, run_as_user)
    tmp = Path("/tmp/jellyfin-shim-manager-sudoers")
    tmp.write_text(content)
    subprocess.run(["sudo", "visudo", "-cf", str(tmp)], check=True)
    subprocess.run(["sudo", "install", "-m", "0440", str(tmp), str(SUDOERS_PATH)], check=True)
    tmp.unlink(missing_ok=True)


def _write_unit(path: Path, content: str):
    tmp = Path(f"/tmp/{path.name}")
    tmp.write_text(content)
    subprocess.run(["sudo", "install", "-m", "0644", str(tmp), str(path)], check=True)
    tmp.unlink(missing_ok=True)
